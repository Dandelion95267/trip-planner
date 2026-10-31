"""旅游助手的 FastAPI 入口。"""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Path as PathParameter, Query
from fastapi.staticfiles import StaticFiles

from app.agents.trip_planner_agent import TripPlannerAgent, TripPlanningError
from app.config import get_settings
from app.models.schemas import POIInfo, POISearchRequest, RoutePlan, RouteRequest, TripPlan, TripRequest, WeatherInfo
from app.services.amap_service import AmapConfigurationError, AmapRouteNotFoundError, AmapService, AmapServiceError
from app.services.trip_workflow import TripPlanningWorkflow
from app.services.budget_service import estimate_budget


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.app_debug,
)


@app.get("/api/health")
def health_check() -> dict[str, str]:
    """供前端检查连接，不调用模型或地图服务。"""
    return {"status": "ok", "app": settings.app_name}


@app.post("/api/trip/validate", response_model=TripRequest)
async def validate_trip_request(request: TripRequest) -> TripRequest:
    """校验旅行需求，返回通过验证后的数据。"""
    return request


@app.post("/api/trip/plan", response_model=TripPlan)
async def create_trip_plan(request: TripRequest) -> TripPlan:
    """结合高德景点、天气、候选住宿与餐馆生成行程。"""
    try:
        plan = await TripPlanningWorkflow(planner=TripPlannerAgent()).plan_trip(request)
        # 工作流结束后才计算，确保使用最终版本；不接受模型自行生成的费用合计。
        plan.budget = estimate_budget(plan, request.budget_options) if request.budget_options is not None else None
        return plan
    except AmapConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except AmapServiceError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except TripPlanningError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=503, detail="大模型配置不完整，请检查后端配置。") from error


@app.post("/api/map/poi", response_model=list[POIInfo])
async def search_places(request: POISearchRequest) -> list[POIInfo]:
    """通过高德 MCP 按城市和关键词搜索地点。"""
    try:
        return await AmapService().search_poi(
            keywords=request.keywords, city=request.city, citylimit=request.citylimit
        )
    except AmapConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except AmapServiceError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.get("/api/map/weather", response_model=list[WeatherInfo])
async def get_city_weather(city: str = Query(..., min_length=1, max_length=100)) -> list[WeatherInfo]:
    """查询指定城市当前可用的天气预报。"""
    city = city.strip()
    if not city:
        raise HTTPException(status_code=422, detail="城市不能为空。")
    try:
        return await AmapService().get_weather(city=city)
    except AmapConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except AmapServiceError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.get("/api/map/hotels", response_model=list[POIInfo])
async def search_hotels(
    city: str = Query(..., min_length=1, max_length=100),
    preference: str = Query(default="", max_length=100),
) -> list[POIInfo]:
    """搜索候选住宿地点，不提供实时房价、房态或预订。"""
    city = city.strip()
    if not city:
        raise HTTPException(status_code=422, detail="城市不能为空。")
    try:
        return await AmapService().search_hotels(city=city, preference=preference)
    except AmapConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except AmapServiceError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.get("/api/map/restaurants", response_model=list[POIInfo])
async def search_restaurants(
    city: str = Query(..., min_length=1, max_length=100),
    preference: str = Query(default="", max_length=100),
) -> list[POIInfo]:
    """搜索候选餐馆，不提供实时消费、菜单或营业时段信息。"""
    city = city.strip()
    if not city:
        raise HTTPException(status_code=422, detail="城市不能为空。")
    try:
        return await AmapService().search_restaurants(city=city, preference=preference)
    except AmapConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except AmapServiceError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.post("/api/map/route", response_model=RoutePlan)
async def query_route(request: RouteRequest) -> RoutePlan:
    """查询两地点之间的步行、驾车或同城公共交通路线。"""
    try:
        return await AmapService().plan_route(request)
    except AmapConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except AmapRouteNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except AmapServiceError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.get("/api/map/poi/{poi_id}", response_model=POIInfo)
async def get_place_detail(
    poi_id: str = PathParameter(..., min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"),
) -> POIInfo:
    """查询地图地点详情，供前端补齐餐馆、住宿等地点的坐标。"""
    try:
        return await AmapService().get_poi_detail(poi_id)
    except AmapConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except AmapServiceError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


# 前端构建后，由同一个后端服务提供页面；API 路由优先于静态文件。
# 开发时也可使用 Vite 的 /api 代理，无需把任何 Key 放进前端。
FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"
if (FRONTEND_DIST / "index.html").is_file():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
