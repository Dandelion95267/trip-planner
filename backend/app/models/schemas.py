from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.route_policy import resolve_route_mode


class BudgetOptions(BaseModel):
    """用户填写的费用估算标准，人民币整数金额；不代表实际报价。"""

    travelers: int = Field(default=1, ge=1, le=50, description="出行人数")
    rooms: int = Field(default=1, ge=1, le=50, description="每晚预估房间数")
    hotel_per_room_night: Optional[int] = Field(default=None, ge=0, le=1000000, description="每间每晚住宿费用（元）")
    meals_per_person_day: Optional[int] = Field(default=None, ge=0, le=1000000, description="每人每天全部餐饮费用（元）")
    transport_per_person_day: Optional[int] = Field(default=None, ge=0, le=1000000, description="每人每天市内交通费用（元）")
    ticket_per_person_visit: Optional[int] = Field(default=None, ge=0, le=1000000, description="每人每次景点游览的平均门票费用（元）")


class TripRequest(BaseModel):
    """用户提交的旅行规划请求。"""

    city: str = Field(..., description="目的地城市")
    start_date: str = Field(..., description="开始日期，格式为YYYY-MM-DD")
    end_date: str = Field(..., description="结束日期，格式为YYYY-MM-DD")
    travel_days: int = Field(..., description="旅行天数", ge=1, le=30)
    transportation: str = Field(..., description="交通方式")
    route_mode: Optional[Literal["auto", "walking", "driving", "transit"]] = Field(
        default=None, description="景点间路线方式；auto比较步行与公共交通，未填时根据交通方式识别"
    )
    max_leg_walking_m: Optional[int] = Field(
        default=None, ge=0, le=50000,
        description="每段相邻景点路线的最大步行米数；公交含接驳步行。不含景点内部、酒店或餐馆接驳；仅适用于自动、步行、公共交通"
    )
    preserve_first_attraction: bool = Field(default=False, description="程序修复路线时保留本轮行程每天首站；不等于预约锁定。未设置时允许为满足步行约束更换首站")
    optimize_order: bool = Field(
        default=False, description="是否按地图坐标优化景点顺序，固定首个景点；仅用于无固定预约顺序的行程"
    )
    enable_review: bool = Field(default=False, description="是否审核行程并按意见修改，最多修改两轮，会增加等待和模型调用")
    budget_options: Optional[BudgetOptions] = Field(default=None, description="费用估算标准；未填时不计算预算，不作为行程筛选或限额条件")
    accommodation: str = Field(..., description="住宿偏好")
    dining_preference: str = Field(default="", max_length=100, description="餐饮偏好，例如本地菜、面食")
    preferences: List[str] = Field(default_factory=list, description="旅行偏好标签")
    free_text_input: Optional[str] = Field(default="", description="额外要求")

    @model_validator(mode="after")
    def validate_dates(self) -> "TripRequest":
        """检查日期合法性，以及包含首尾两天的旅行天数。"""
        try:
            start = date.fromisoformat(self.start_date)
            end = date.fromisoformat(self.end_date)
        except ValueError as error:
            raise ValueError("开始日期和结束日期必须是有效日期，格式为YYYY-MM-DD。") from error

        if start.isoformat() != self.start_date or end.isoformat() != self.end_date:
            raise ValueError("日期格式必须为YYYY-MM-DD。")
        if end < start:
            raise ValueError("结束日期不能早于开始日期。")
        if (end - start).days + 1 != self.travel_days:
            raise ValueError("旅行天数必须与开始日期、结束日期一致，包含首尾两天。")
        if self.max_leg_walking_m is not None and resolve_route_mode(self.transportation, self.route_mode) not in {"auto", "walking", "transit"}:
            raise ValueError("步行上限仅适用于自动、步行或公共交通，请明确选择交通方式。")
        return self


class Location(BaseModel):
    """位置信息（经纬度坐标）。"""

    longitude: float = Field(..., description="经度", ge=-180, le=180)
    latitude: float = Field(..., description="纬度", ge=-90, le=90)


class POIInfo(BaseModel):
    """高德返回的地点信息，未提供坐标时保留为空。"""

    id: str = Field(..., description="高德地点ID")
    name: str = Field(..., description="地点名称")
    type: str = Field(default="", description="地点类别")
    address: str = Field(default="", description="地点地址")
    location: Optional[Location] = Field(default=None, description="高德经纬度坐标")


class RouteRequest(BaseModel):
    """使用高德地点ID查询两点之间的路线。"""

    origin_poi_id: str = Field(..., min_length=1, max_length=100, description="起点的高德地点ID")
    destination_poi_id: str = Field(..., min_length=1, max_length=100, description="终点的高德地点ID")
    mode: Literal["walking", "driving", "transit"] = Field(default="walking", description="步行、驾车或公共交通")
    max_leg_walking_m: Optional[int] = Field(default=None, ge=0, le=50000, description="本段步行米数上限，缺少步行数据的方案不能视为符合要求")
    city: str = Field(default="", max_length=100, description="公共交通查询必填，目前支持同城查询")

    @field_validator("origin_poi_id", "destination_poi_id", "city")
    @classmethod
    def strip_route_text(cls, value: str, info):
        value = value.strip()
        if info.field_name != "city" and not value:
            raise ValueError("起点和终点的地点ID不能为空。")
        return value

    @model_validator(mode="after")
    def validate_route(self) -> "RouteRequest":
        if self.origin_poi_id == self.destination_poi_id:
            raise ValueError("起点和终点不能是同一个地点。")
        if self.mode == "transit" and not self.city:
            raise ValueError("公共交通路线查询必须提供城市。")
        if self.max_leg_walking_m is not None and self.mode == "driving":
            raise ValueError("驾车路线不能核验完整步行接驳，请勿同时设置步行上限。")
        return self


class RoutePlan(BaseModel):
    """高德返回的第一条路线方案，耗时为查询时的估计。"""

    origin: POIInfo = Field(..., description="起点详情")
    destination: POIInfo = Field(..., description="终点详情")
    mode: Literal["walking", "driving", "transit"]
    distance_m: Optional[int] = Field(default=None, ge=0, description="路线距离（米），缺少可靠数据时为空")
    duration_s: int = Field(..., ge=0, description="预计耗时（秒），不保证未来出行时的耗时")
    walking_distance_m: Optional[int] = Field(default=None, ge=0, description="公共交通方案中的步行距离（米）")
    steps: List[str] = Field(default_factory=list, description="主要路线说明")
    selection_reason: Optional[str] = Field(default=None, description="自动交通模式的推荐依据")
    warnings: List[str] = Field(default_factory=list, description="方案比较不完整或较长步行等提示")


class POISearchRequest(BaseModel):
    """地点搜索条件。"""

    keywords: str = Field(..., min_length=1, max_length=100, description="搜索关键词，例如故宫")
    city: str = Field(..., min_length=1, max_length=100, description="搜索城市，例如北京")
    citylimit: bool = Field(default=True, description="是否限制在该城市内搜索")

    @field_validator("keywords", "city")
    @classmethod
    def strip_search_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("搜索关键词和城市不能为空。")
        return value


class Attraction(BaseModel):
    """景点信息。"""

    poi_id: Optional[str] = Field(default=None, description="对应的高德地点ID")
    name: str = Field(..., description="景点名称")
    address: str = Field(..., description="地址")
    location: Optional[Location] = Field(default=None, description="高德提供的经纬度坐标，未知时为空")
    visit_duration: int = Field(..., description="建议游览时间（分钟）", gt=0)
    description: str = Field(..., description="景点描述")
    category: Optional[str] = Field(default="景点", description="景点类别")
    rating: Optional[float] = Field(default=None, ge=0, le=5, description="评分")
    image_url: Optional[str] = Field(default=None, description="图片URL")
    ticket_price: Optional[int] = Field(default=None, ge=0, description="门票价格（元），未知时为空")


class DiningPreferenceEvidence(BaseModel):
    preference: str
    kind: Literal["cuisine", "taste", "dietary", "other"]
    target_category: Optional[str] = None
    strict: bool = False
    status: Literal["supported", "hint", "unknown", "conflict"]
    source: Literal["poi_type", "poi_name", "not_available"]
    value: Optional[str] = None
    explanation: str


class Meal(BaseModel):
    """行程中的候选餐馆，消费和营业情况需要另行确认。"""

    poi_id: Optional[str] = Field(default=None, description="对应的高德地点ID")
    type: Literal["breakfast", "lunch", "dinner", "snack"] = Field(..., description="用餐时段")
    name: str = Field(..., description="餐饮名称")
    address: Optional[str] = Field(default=None, description="地址")
    location: Optional[Location] = Field(default=None, description="经纬度坐标")
    description: Optional[str] = Field(default=None, description="描述")
    estimated_cost: Optional[int] = Field(default=None, ge=0, description="用餐费用（元），未知时为空")
    anchor_poi_id: Optional[str] = Field(default=None, description="餐馆筛选所依据的最终景点ID")
    straight_line_distance_m: Optional[int] = Field(default=None, ge=0, description="到参考景点的估算直线距离，不是步行或道路距离")
    poi_category: Optional[str] = Field(default=None, description="同次地图搜索或详情给出的餐馆类别，不是菜单证据")
    preference_level: Optional[Literal["category_supported", "name_hint", "unverified", "not_requested"]] = None
    preference_evidence: List[DiningPreferenceEvidence] = Field(default_factory=list)
    preference_summary: Optional[str] = None


class Hotel(BaseModel):
    """地图检索到的候选住宿，不代表价格或房态已经确认。"""

    poi_id: Optional[str] = Field(default=None, description="对应的高德地点ID")
    name: str = Field(..., description="酒店名称")
    address: str = Field(default="", description="酒店地址")
    location: Optional[Location] = Field(default=None, description="酒店位置")
    price_range: Optional[str] = Field(default=None, description="价格范围，未查询时为空")
    rating: Optional[str] = Field(default=None, description="评分，未查询时为空")
    distance: Optional[str] = Field(default=None, description="距离景点距离，未计算时为空")
    type: Optional[str] = Field(default=None, description="高德提供的住宿类别")
    estimated_cost: Optional[int] = Field(default=None, ge=0, description="费用（元/晚），未知时为空")


class BudgetItem(BaseModel):
    """单项费用的估算依据，未知金额保留为空；数量为0时小计为0。"""

    category: Literal["attractions", "hotels", "meals", "transportation"]
    label: str
    quantity: int = Field(..., ge=0)
    unit_price: Optional[int] = Field(default=None, ge=0)
    subtotal: Optional[int] = Field(default=None, ge=0)
    basis: str = Field(..., description="数量和单价的计算说明")


class Budget(BaseModel):
    """根据用户标准和最终行程计算，未知类别不按免费处理。"""

    source: Literal["user_estimate"] = "user_estimate"
    currency: Literal["CNY"] = "CNY"
    travelers: int = Field(default=1, ge=1, le=50)
    rooms: int = Field(default=1, ge=1, le=50)
    nights: int = Field(default=0, ge=0, le=29)
    total_attractions: Optional[int] = Field(default=None, ge=0, description="门票估算合计，未知时为空")
    total_hotels: Optional[int] = Field(default=None, ge=0, description="住宿估算合计，未知时为空")
    total_meals: Optional[int] = Field(default=None, ge=0, description="餐饮估算合计，未知时为空")
    total_transportation: Optional[int] = Field(default=None, ge=0, description="市内交通估算合计，未知时为空")
    known_subtotal: int = Field(default=0, ge=0, description="可计算项目的小计，不代表完整旅行花费")
    is_complete: bool = Field(default=False, description="四类费用是否均可计算，不代表覆盖所有旅行开销")
    total: Optional[int] = Field(default=None, ge=0, description="四类估算合计；有未知类别时为空")
    items: List[BudgetItem] = Field(default_factory=list, description="逐项计算明细")
    notes: List[str] = Field(default_factory=list, description="估算范围和未包含费用说明")


class OrderOptimization(BaseModel):
    """顺序优化的依据与结果，距离是坐标间直线距离，不是道路里程。"""

    status: Literal["optimized", "unchanged", "skipped"]
    distance_basis: Literal["straight_line"] = "straight_line"
    original_order: List[str] = Field(default_factory=list, description="调整前的景点名称顺序")
    optimized_order: List[str] = Field(default_factory=list, description="调整后的景点名称顺序")
    before_distance_m: Optional[int] = Field(default=None, ge=0, description="调整前各相邻点直线距离之和（米）")
    after_distance_m: Optional[int] = Field(default=None, ge=0, description="调整后各相邻点直线距离之和（米）")
    reason: str = Field(..., description="调整或保持原顺序的原因")


class UnresolvedMeal(BaseModel):
    meal_type: Literal["lunch", "dinner"]
    anchor_poi_id: Optional[str] = None
    anchor_name: str
    reason: Literal["no_coordinates", "no_nearby_candidate", "provider_error", "timeout", "budget_exhausted", "preference_conflict", "preference_unverified"]
    explanation: str


class DiningAlternative(BaseModel):
    kind: Literal["retry", "manual_search", "adjust_preferences", "contact_restaurant"]
    title: str
    description: str
    requires_confirmation: Literal[True] = True
    verification: Literal["not_verified"] = "not_verified"


class DiningResolution(BaseModel):
    """餐馆候选交付，不代表菜单、口味、营业或接驳已经确认。"""

    status: Literal["candidates_available", "partial", "needs_choice", "not_applicable"]
    requested_preference: str
    preference_verification: Literal["not_requested", "not_verified"]
    based_on_provisional_itinerary: bool
    required_slots: int = Field(..., ge=0, le=2)
    provided_slots: int = Field(..., ge=0, le=2)
    summary: str
    preference_notes: List[str] = Field(default_factory=list)
    unresolved: List[UnresolvedMeal] = Field(default_factory=list)
    alternatives: List[DiningAlternative] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_slots(self) -> "DiningResolution":
        missing = self.required_slots - self.provided_slots
        if missing < 0 or missing != len(self.unresolved):
            raise ValueError("餐饮候选数量必须与未安排时段一致。")
        expected = ("not_applicable" if self.required_slots == 0 else
                    "candidates_available" if missing == 0 else
                    "partial" if self.provided_slots else "needs_choice")
        if self.status != expected or (missing and not self.alternatives):
            raise ValueError("餐饮交付状态、数量和替代建议不一致。")
        if not missing and self.alternatives and (not self.preference_notes or any(x.kind != "contact_restaurant" for x in self.alternatives)):
            raise ValueError("候选齐全时只能提供有偏好说明支持的商家核实建议。")
        return self


class DayPlan(BaseModel):
    """单日行程。"""

    date: str = Field(..., description="日期")
    day_index: int = Field(..., description="第几天（从0开始）")
    description: str = Field(..., description="当日行程描述")
    transportation: str = Field(..., description="交通方式")
    accommodation: str = Field(..., description="住宿安排")
    hotel: Optional[Hotel] = Field(default=None, description="酒店信息")
    attractions: List[Attraction] = Field(default_factory=list, description="景点列表")
    meals: List[Meal] = Field(default_factory=list, description="餐饮安排")
    dining_warnings: List[str] = Field(default_factory=list, description="餐饮搜索、坐标或接驳尚未核验的提示")
    dining_resolution: Optional[DiningResolution] = Field(default=None, description="后端生成的当前景点餐饮候选状态、原因和待选择做法，模型不可生成")
    routes: List[RoutePlan] = Field(default_factory=list, description="按行程顺序查询的相邻景点路线，不含酒店和餐馆接驳")
    route_warnings: List[str] = Field(default_factory=list, description="未能查询的路段或不支持的出行方式提示")
    order_optimization: Optional[OrderOptimization] = Field(default=None, description="主动开启后的景点顺序优化结果")


class WeatherInfo(BaseModel):
    """天气信息。"""

    date: str = Field(..., description="日期")
    day_weather: str = Field(..., description="白天天气")
    night_weather: str = Field(..., description="夜间天气")
    day_temp: int = Field(..., description="白天温度（摄氏度）")
    night_temp: int = Field(..., description="夜间温度（摄氏度）")
    wind_direction: str = Field(..., description="风向")
    wind_power: str = Field(..., description="风力")

    @field_validator("day_temp", "night_temp", mode="before")
    @classmethod
    def parse_temperature(cls, v):
        """移除温度单位并转换为整数，无法解析时拒绝该数据。"""
        if isinstance(v, str):
            v = v.replace("°C", "").replace("℃", "").replace("°", "").strip()
            return int(v)
        return v


class ReviewAlternative(BaseModel):
    proposal: str = Field(..., min_length=1, description="尚未采用的模型调整方向，不是自动修改指令")
    requires_confirmation: Literal[True] = True
    applied: Literal[False] = False


class TripReviewIssue(BaseModel):
    """审核发现的一项问题，应基于用户需求或已提供的行程数据。"""

    category: Literal["preference", "pace", "consistency"] = Field(
        ..., description="问题类别：用户需求匹配、行程强度、描述与实际安排的一致性"
    )
    day_index: Optional[int] = Field(
        default=None, ge=0, le=29, description="问题对应的天数索引，从0开始；涉及整个行程时为空"
    )
    problem: str = Field(..., min_length=1, description="发现的具体问题")
    evidence: str = Field(..., min_length=1, description="判断依据，引用用户要求、游览时长、路线耗时等已有信息")
    suggestion: str = Field(..., min_length=1, description="供规划Agent执行的具体修改建议")
    source: Literal["program", "model"] = Field(default="model", description="问题由程序核对还是模型提出；后端填写")
    alternative: Optional[ReviewAlternative] = Field(default=None, description="需用户确认的其他方向；后端分离，自动修改不使用")

    @field_validator("problem", "evidence", "suggestion", mode="before")
    @classmethod
    def strip_review_text(cls, value):
        """去除首尾空白，空白文本会被字段的最小长度限制拒绝。"""
        return value.strip() if isinstance(value, str) else value


class ReviewTimeEvidence(BaseModel):
    day_index: int = Field(..., ge=0)
    suggested_visit_minutes: int = Field(..., ge=0)
    expected_route_count: int = Field(..., ge=0)
    known_route_count: int = Field(..., ge=0)
    known_route_duration_s: int = Field(..., ge=0)
    known_total_duration_s: int = Field(..., ge=0, description="建议游览加已知相邻景点交通；非完整全天耗时")
    all_adjacent_routes_available: bool
    display_text: str

    @model_validator(mode="after")
    def validate_totals(self):
        if self.known_total_duration_s != self.suggested_visit_minutes * 60 + self.known_route_duration_s:
            raise ValueError("已知时间合计与原始时长不一致。")
        if self.known_route_count > self.expected_route_count or self.all_adjacent_routes_available != (self.known_route_count == self.expected_route_count):
            raise ValueError("相邻路线覆盖与时间统计不一致。")
        return self


class MealConsistencyCheck(BaseModel):
    day_index: int = Field(..., ge=0)
    meal_type: Literal["breakfast", "lunch", "dinner", "snack"]
    expected_poi_id: Optional[str] = None
    expected_name: str
    mentioned_name: Optional[str] = None
    source_excerpt: Optional[str] = None
    status: Literal["matched", "conflict", "unknown"]
    explanation: str


class TripReviewResult(BaseModel):
    """一次行程审核的结构化结果，不代表开放时间或预约条件已核实。"""

    passed: bool = Field(..., description="是否通过本次审核；存在需要修改的问题时为false")
    summary: str = Field(..., min_length=1, description="审核结论的简短说明")
    issues: List[TripReviewIssue] = Field(default_factory=list, description="需要修改的问题；审核通过时为空")
    time_evidence: List[ReviewTimeEvidence] = Field(default_factory=list, description="后端计算，模型不能填写")
    meal_consistency: List[MealConsistencyCheck] = Field(default_factory=list, description="后端核对，未知不算冲突")
    semantic_status: Optional[Literal["completed", "not_run", "unavailable"]] = None

    @field_validator("summary", mode="before")
    @classmethod
    def strip_summary(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_review_result(self) -> "TripReviewResult":
        """避免出现通过但仍有问题，或者未通过却没有修改依据的矛盾结果。"""
        if self.passed and self.issues:
            raise ValueError("审核通过时，问题列表必须为空。")
        if not self.passed and not self.issues:
            raise ValueError("审核未通过时，必须提供至少一项问题及修改建议。")
        return self


class TripReviewSummary(BaseModel):
    """工作流写入的最终审核状态，不接受规划模型自行生成的结论。"""

    status: Literal["passed", "needs_revision", "unavailable"]
    review_count: int = Field(..., ge=0, le=3, description="审核尝试次数，含失败调用")
    revisions: int = Field(..., ge=0, le=2, description="已成功生成并校验的修改次数")
    result: Optional[TripReviewResult] = Field(default=None, description="当前返回行程的审核结果；未能审核时为空")
    message: str = Field(..., description="流程结束原因或仍需注意的问题")

    @model_validator(mode="after")
    def validate_status(self) -> "TripReviewSummary":
        if self.status == "unavailable":
            if self.result is not None:
                raise ValueError("审核不可用时不能附带其他版本行程的审核结果。")
        elif self.result is None or self.result.passed != (self.status == "passed"):
            raise ValueError("审核状态必须与当前行程的审核结论一致。")
        return self


class POISearchEvidence(BaseModel):
    tool: Literal["maps_text_search", "maps_around_search"]
    parameters: dict[str, str | int]
    collected_at_utc: str
    pois: List[POIInfo]


class POIDetailEvidence(BaseModel):
    collected_at_utc: str
    poi: POIInfo


class CandidateEvidence(BaseModel):
    version: Literal[1] = 1
    request_id: str
    candidates: List[POIInfo] = Field(default_factory=list)
    hotel_candidates: List[POIInfo] = Field(default_factory=list)
    restaurant_candidates: List[POIInfo] = Field(default_factory=list)
    searches: List[POISearchEvidence] = Field(default_factory=list)
    details: List[POIDetailEvidence] = Field(default_factory=list)


class StageTiming(BaseModel):
    generation: int = Field(..., ge=0)
    stage: Literal["context", "model", "order", "routes", "dining", "review"]
    elapsed_s: float = Field(..., ge=0)


class DiningDiagnostic(BaseModel):
    generation: int = Field(..., ge=1)
    day_index: int
    meal_type: Literal["lunch", "dinner"]
    anchor_poi_id: Optional[str] = None
    selected_poi_id: Optional[str] = None
    status: Literal["selected", "no_coordinates", "no_nearby_candidate", "provider_error", "timeout", "budget_exhausted", "routes_pending", "preference_conflict", "preference_unverified"]
    cache_hit: bool = False
    search_relaxed: bool = False
    straight_line_distance_m: Optional[int] = Field(default=None, ge=0)
    query_terms: List[str] = Field(default_factory=list)
    preference_rejections: int = Field(default=0, ge=0)


class UnresolvedRoute(BaseModel):
    """原要求下尚未核验通过的一段路线，不表示该城市无解。"""

    day_index: int = Field(..., ge=0)
    origin_poi_id: Optional[str] = None
    destination_poi_id: Optional[str] = None
    origin_name: str
    destination_name: str
    reason: Literal["no_route", "over_limit", "walking_unknown", "missing_coordinates", "missing_poi_id", "provider_error", "invalid_response", "timeout", "budget_exhausted", "not_queried", "mode_mismatch", "unsupported_mode"]
    explanation: str
    observed_walking_m: Optional[int] = Field(default=None, ge=0, description="仅限本次返回方案中的已知距离，不是所有可行方案的下界")


class RouteAlternative(BaseModel):
    """待用户选择的调整建议，不是已验证的替代行程。"""

    kind: Literal["retry", "change_attractions", "relax_walking_limit", "change_transport"]
    title: str
    description: str
    condition_changes: List[str] = Field(default_factory=list)
    suggested_max_leg_walking_m: Optional[int] = Field(default=None, ge=0, le=50000)
    requires_confirmation: Literal[True] = True
    verification: Literal["not_verified"] = "not_verified"


class RouteResolution(BaseModel):
    """程序生成的路线交付状态，与模型语义审核和原硬约束评分分开。"""

    status: Literal["verified", "needs_choice", "not_applicable"]
    scope: Literal["adjacent_attractions"] = "adjacent_attractions"
    requested_transportation: str
    requested_mode: Optional[Literal["auto", "walking", "transit", "driving"]] = None
    max_leg_walking_m: Optional[int] = Field(default=None, ge=0, le=50000)
    preserve_first_attraction: bool = False
    required_segments: int = Field(..., ge=0)
    verified_segments: int = Field(..., ge=0)
    summary: str
    limitations: List[str] = Field(default_factory=list)
    unresolved: List[UnresolvedRoute] = Field(default_factory=list)
    alternatives: List[RouteAlternative] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_coverage(self) -> "RouteResolution":
        missing = self.required_segments - self.verified_segments
        if missing < 0 or missing != len(self.unresolved):
            raise ValueError("路线核验数量必须与缺失路段一致。")
        if self.status == "needs_choice":
            if missing == 0 or not self.alternatives:
                raise ValueError("待选择状态必须有未核验路段和可选建议。")
        elif self.alternatives or missing or (self.status == "verified") != (self.required_segments > 0):
            raise ValueError("路线状态与核验范围不一致。")
        return self


class TripPlan(BaseModel):
    """完整旅行计划。"""

    city: str = Field(..., description="目的地城市")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    days: List[DayPlan] = Field(default_factory=list, description="每日行程")
    weather_info: List[WeatherInfo] = Field(default_factory=list, description="天气信息")
    overall_suggestions: str = Field(..., description="总体建议")
    budget: Optional[Budget] = Field(default=None, description="预算信息")
    review: Optional[TripReviewSummary] = Field(default=None, description="后端审核流程的结果；未开启时为空")
    route_diagnostics: List["RouteDiagnostic"] = Field(default_factory=list, description="后端记录的本次路线查询与缓存诊断，供评测分析，不由模型生成")
    request_id: Optional[str] = Field(default=None, description="后端生成的本次请求标识")
    candidate_evidence: Optional[CandidateEvidence] = Field(default=None, description="同次工具查询的来源快照，由后端填写")
    stage_timings: List[StageTiming] = Field(default_factory=list, description="各生成轮次的阶段耗时，不代表token或费用")
    dining_diagnostics: List[DiningDiagnostic] = Field(default_factory=list)
    route_resolution: Optional[RouteResolution] = Field(default=None, description="后端生成的路线核验状态、原因和待用户选择的替代建议，模型不可自行生成")


class RouteDiagnostic(BaseModel):
    generation: int = Field(..., ge=1)
    origin_poi_id: str
    destination_poi_id: str
    mode: Literal["auto", "walking", "transit", "driving"]
    max_leg_walking_m: Optional[int] = None
    status: Literal["available", "over_limit", "walking_unknown", "no_route", "missing_coordinates", "provider_error", "invalid_response", "timeout", "budget_exhausted"]
    cache_hit: bool = False
    attempt: int = Field(default=0, ge=0)
    walking_distance_m: Optional[int] = Field(default=None, ge=0)
    minimum_returned_walking_m: Optional[int] = Field(default=None, ge=0)
    message: str


TripPlan.model_rebuild()
