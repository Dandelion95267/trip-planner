"""通过高德官方 MCP 服务搜索地点、查询天气和路线。"""

import asyncio
import json
from datetime import date
from urllib.parse import urlencode

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from app.config import get_settings
from app.models.schemas import Location, POIInfo, RoutePlan, RouteRequest, WeatherInfo
from app.services.route_policy import walking_distance
from app.services.source_evidence import SourceEvidenceRecorder


class AmapConfigurationError(RuntimeError):
    """尚未配置高德 API Key。"""


class AmapServiceError(RuntimeError):
    """地图服务请求失败或返回了无法解析的数据。"""

    def __init__(self, message: str, *, code: str = "provider_error"):
        super().__init__(message)
        self.code = code


class AmapRouteNotFoundError(AmapServiceError):
    """地点缺少坐标或高德没有返回可用路线。"""

    def __init__(self, message: str, *, code: str = "no_route"):
        super().__init__(message, code=code)


class AmapWalkingLimitError(AmapRouteNotFoundError):
    """没有可验证且满足本段步行上限的方案。"""

    def __init__(self, message: str, *, code: str = "walking_unknown", minimum_walking_m: int | None = None):
        super().__init__(message, code=code)
        self.minimum_walking_m = minimum_walking_m


class AmapService:
    """管理 MCP 连接，并把搜索结果转换为项目的数据模型。"""

    def __init__(self):
        # 当前应用每次请求创建服务实例，这里只复用本次请求查过的地点详情。
        self._poi_details: dict[str, POIInfo] = {}
        self.source_evidence = SourceEvidenceRecorder()

    async def search_poi(
        self, keywords: str, city: str, citylimit: bool = True
    ) -> list[POIInfo]:
        result = await self._call_tool("maps_text_search", {
            "keywords": keywords,
            "city": city,
            "citylimit": str(citylimit).lower(),
        })
        try:
            pois = self._parse_pois(result)
            self.source_evidence.search("maps_text_search", {
                "keywords": keywords, "city": city, "citylimit": str(citylimit).lower(),
            }, pois)
            return pois
        except Exception:
            raise AmapServiceError("高德地点搜索返回的数据无法解析，请稍后重试。") from None

    async def get_weather(self, city: str) -> list[WeatherInfo]:
        """查询高德当前可提供的逐日天气预报。"""
        result = await self._call_tool("maps_weather", {"city": city})
        try:
            return self._parse_weather(result)
        except Exception:
            raise AmapServiceError("高德天气返回的数据无法解析，请稍后重试。") from None

    async def search_hotels(self, city: str, preference: str = "") -> list[POIInfo]:
        """按住宿偏好搜索候选地点，结果不包含实时房价、房态。"""
        keywords = preference.strip()
        if not keywords:
            keywords = "酒店"
        elif not any(word in keywords for word in ("酒店", "宾馆", "民宿", "旅馆", "青旅")):
            keywords += " 酒店"
        places = await self.search_poi(keywords=keywords, city=city)
        if not places and keywords != "酒店":
            # 偏好关键词可能过窄；一般候选仍需用户确认是否符合要求。
            places = await self.search_poi(keywords="酒店", city=city)
        return places

    async def search_restaurants(self, city: str, preference: str = "") -> list[POIInfo]:
        """按城市和关键词搜索候选餐馆，不查询菜单、营业时间或消费。"""
        keywords = preference.strip()
        if not keywords:
            keywords = "餐馆"
        elif not any(word in keywords for word in ("餐厅", "餐馆", "饭店", "小吃", "美食")):
            keywords += " 餐馆"
        # 有偏好但未搜到结果时返回空列表，避免扩大搜索后丢失用餐要求。
        return await self.search_poi(keywords=keywords, city=city)

    async def search_restaurants_near(self, location: Location, preference: str = "", radius: int = 2000) -> list[POIInfo]:
        """使用官方MCP周边搜索；半径用于候选召回，不代表实际步行距离。"""
        keywords = preference.strip() or "餐馆"
        if not any(word in keywords for word in ("餐厅", "餐馆", "饭店", "小吃", "美食")):
            keywords += " 餐馆"
        arguments = {"keywords": keywords,
                     "location": f"{location.longitude},{location.latitude}", "radius": str(radius)}
        result = await self._call_tool("maps_around_search", arguments)
        try:
            pois = self._parse_pois(result)
        except Exception:
            raise AmapServiceError("高德周边餐馆结果无法解析。", code="invalid_response") from None
        self.source_evidence.search("maps_around_search", arguments, pois)
        self.source_evidence.register("restaurant_candidates", pois)
        return pois

    async def get_poi_detail(self, poi_id: str) -> POIInfo:
        """按地点ID查询详情，获取搜索列表中可能缺少的坐标。"""
        if poi_id in self._poi_details:
            return self._poi_details[poi_id].model_copy(deep=True)
        result = await self._call_tool("maps_search_detail", {"id": poi_id})
        try:
            data = self._parse_result(result)
            if data.get("id") != poi_id:
                raise ValueError("详情返回的地点ID与请求不一致。")
            place = POIInfo(
                id=data["id"], name=data["name"], address=data.get("address") or "",
                type=data.get("type") or "", location=self._parse_location(data.get("location")),
            )
            self._poi_details[poi_id] = place
            self.source_evidence.detail(place)
            return place.model_copy(deep=True)
        except Exception:
            raise AmapServiceError("高德地点详情无法解析，请检查地点ID后重试。") from None

    async def plan_route(self, request: RouteRequest) -> RoutePlan:
        """查询路线；设置步行上限时从返回方案中筛选，不调用大模型。"""
        origin = await self.get_poi_detail(request.origin_poi_id)
        destination = await self.get_poi_detail(request.destination_poi_id)
        if origin.location is None or destination.location is None:
            raise AmapRouteNotFoundError("起点或终点缺少地图坐标，无法查询路线。", code="missing_coordinates")
        arguments = {
            "origin": f"{origin.location.longitude},{origin.location.latitude}",
            "destination": f"{destination.location.longitude},{destination.location.latitude}",
        }
        tools = {
            "walking": "maps_direction_walking",
            "driving": "maps_direction_driving",
            "transit": "maps_direction_transit_integrated",
        }
        if request.mode == "transit":
            arguments.update(city=request.city, cityd=request.city)
        result = await self._call_tool(tools[request.mode], arguments)
        try:
            return self._parse_route(result, origin, destination, request.mode, request.max_leg_walking_m)
        except AmapRouteNotFoundError:
            raise
        except Exception:
            raise AmapServiceError("高德路线结果无法解析，请稍后重试。", code="invalid_response") from None

    @staticmethod
    def _parse_route(result: CallToolResult, origin: POIInfo, destination: POIInfo, mode: str, max_leg_walking_m: int | None = None) -> RoutePlan:
        data = AmapService._parse_result(result)
        # 步行结果有 route 外层，驾车和公共交通工具目前直接返回路线对象。
        route = data.get("route", data)
        options = route.get("transits" if mode == "transit" else "paths")
        if not isinstance(options, list):
            raise ValueError("路线结果缺少方案列表。")
        if not options:
            raise AmapRouteNotFoundError("高德未找到可用路线，请更换地点或出行方式。")
        if max_leg_walking_m is None:
            return AmapService._parse_route_option(options[0], origin, destination, mode)
        eligible = []
        known_distances = []
        unknown_count = 0
        for option in options:
            try:
                plan = AmapService._parse_route_option(option, origin, destination, mode)
            except (ValueError, TypeError, KeyError, AttributeError):
                unknown_count += 1
                continue
            distance = walking_distance(plan)
            if distance is None:
                unknown_count += 1
            else:
                known_distances.append(distance)
            if distance is not None and distance <= max_leg_walking_m:
                eligible.append(plan)
        if not eligible:
            code = "walking_unknown" if unknown_count else "over_limit"
            raise AmapWalkingLimitError(
                f"返回方案中没有可验证且步行不超过{max_leg_walking_m}米的路线。",
                code=code, minimum_walking_m=min(known_distances) if known_distances else None,
            )
        selected = min(eligible, key=lambda item: (item.duration_s, walking_distance(item)))
        selected.selection_reason = f"从返回方案中筛选每段步行不超过{max_leg_walking_m}米的路线；本方案步行{walking_distance(selected)}米，按预计耗时选择。"
        return selected

    @staticmethod
    def _parse_route_option(option: dict, origin: POIInfo, destination: POIInfo, mode: str) -> RoutePlan:
        """解析单个方案；未知距离保留为空。"""
        distance = option.get("distance")
        duration = AmapService._parse_metric(option["duration"])
        walking_distance = None
        steps = []
        if mode == "transit":
            # 公交顶层 distance 可能是两点直线距离，不能用作方案总里程。
            if option.get("walking_distance") is not None:
                walking_distance = AmapService._parse_metric(option["walking_distance"])
            for segment in option.get("segments", []):
                steps.extend(step["instruction"] for step in segment.get("walking", {}).get("steps", []) if step.get("instruction"))
                buslines = segment.get("bus", {}).get("buslines", [])
                if buslines:
                    bus = buslines[0]
                    departure = bus.get("departure_stop", {}).get("name", "")
                    arrival = bus.get("arrival_stop", {}).get("name", "")
                    steps.append(f"乘坐{bus['name']}：{departure} → {arrival}")
                railway = segment.get("railway", {})
                if railway.get("name"):
                    steps.append(f"乘坐{railway['name']}")
        else:
            if distance is None:
                raise ValueError("步行或驾车路线缺少距离。")
            steps = [step["instruction"] for step in option.get("steps", []) if step.get("instruction")]
        return RoutePlan(
            origin=origin, destination=destination, mode=mode,
            distance_m=AmapService._parse_metric(distance) if distance is not None else None,
            duration_s=duration, walking_distance_m=walking_distance, steps=steps,
        )

    @staticmethod
    def _parse_metric(value) -> int:
        """距离和耗时必须是非负整数，不能把未知值转换为零。"""
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError("路线数值必须是整数或整数字符串。")
        number = int(value)
        if number < 0:
            raise ValueError("路线数值不能为负数。")
        return number

    @staticmethod
    def _parse_location(coordinates) -> Location | None:
        if isinstance(coordinates, str) and coordinates.strip():
            longitude, latitude = coordinates.split(",")
            return Location(longitude=float(longitude), latitude=float(latitude))
        if isinstance(coordinates, dict):
            return Location.model_validate(coordinates)
        return None

    async def _call_tool(self, tool_name: str, arguments: dict) -> CallToolResult:
        """地点和天气查询共用同一种 MCP 连接方式。"""
        api_key = get_settings().amap_api_key.get_secret_value().strip()
        if not api_key:
            raise AmapConfigurationError("请先在 backend/.env 中填写 AMAP_API_KEY。")

        # Key 只从后端配置读取，连接到高德官方地址。
        url = "https://mcp.amap.com/mcp?" + urlencode({"key": api_key})
        try:
            # 每次请求独立连接，结束后自动关闭，避免跨请求共享会话。
            async with asyncio.timeout(45):
                async with httpx.AsyncClient(timeout=30) as client:
                    async with streamable_http_client(url, http_client=client) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            result = await session.call_tool(
                                tool_name, arguments=arguments,
                            )
            return result
        except Exception:
            # 原始网络异常可能包含带 Key 的 URL，不向接口调用方暴露。
            raise AmapServiceError("高德服务调用失败，请检查 Key、服务权限或网络后重试。") from None

    @staticmethod
    def _parse_result(result: CallToolResult) -> dict:
        """兼容 MCP 的结构化结果和文本 JSON 结果。"""
        if result.isError:
            raise ValueError("高德工具执行失败。")

        data = result.structuredContent
        if data is None:
            text = "\n".join(
                block.text for block in result.content if block.type == "text"
            )
            data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("工具结果必须是 JSON 对象。")
        if data.get("status") in (0, "0") or data.get("success") is False:
            raise ValueError("高德拒绝了请求。")
        return data

    @staticmethod
    def _parse_pois(result: CallToolResult) -> list[POIInfo]:
        """将地点搜索结果转换为地点数据类。"""
        data = AmapService._parse_result(result)
        # 缺少 pois 表示返回结构异常，不能当成“搜索结果为空”。
        raw_pois = data.get("pois")
        if not isinstance(raw_pois, list):
            raise ValueError("地点搜索结果缺少 pois 列表。")

        pois = []
        for item in raw_pois:
            pois.append(POIInfo(
                id=item["id"],
                name=item["name"],
                type=item.get("type") or "",
                address=item.get("address") or "",
                location=AmapService._parse_location(item.get("location")),
            ))
        return pois

    @staticmethod
    def _parse_weather(result: CallToolResult) -> list[WeatherInfo]:
        """把高德逐日预报转换成项目的天气数据类。"""
        data = AmapService._parse_result(result)
        forecasts = data.get("forecasts")
        if not isinstance(forecasts, list):
            raise ValueError("天气结果缺少 forecasts 列表。")

        weather = []
        seen_dates = set()
        for item in forecasts:
            forecast_date = date.fromisoformat(item["date"]).isoformat()
            if forecast_date in seen_dates:
                raise ValueError("天气结果存在重复日期。")
            seen_dates.add(forecast_date)
            weather.append(WeatherInfo(
                date=forecast_date,
                day_weather=item["dayweather"],
                night_weather=item["nightweather"],
                day_temp=item["daytemp"],
                night_temp=item["nighttemp"],
                wind_direction=f"白天{item['daywind']}，夜间{item['nightwind']}",
                wind_power=f"白天{item['daypower']}，夜间{item['nightpower']}",
            ))
        return sorted(weather, key=lambda item: item.date)
