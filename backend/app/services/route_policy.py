"""交通模式与每段步行距离规则；不访问网络。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.schemas import RoutePlan


def resolve_route_mode(transportation: str, route_mode: str | None = None) -> str | None:
    return route_mode or {
        "步行": "walking", "walking": "walking",
        "自驾": "driving", "驾车": "driving", "开车": "driving",
        "打车": "driving", "出租车": "driving", "driving": "driving",
        "公共交通": "transit", "公交": "transit", "地铁": "transit",
        "公交地铁": "transit", "transit": "transit",
        "自动": "auto", "自动选择": "auto", "智能推荐": "auto", "auto": "auto",
    }.get(transportation.strip().lower())


def walking_distance(route: RoutePlan) -> int | None:
    """仅比较景点间路线中的步行，不推测驾车停车接驳或景点内部步行。"""
    if route.mode == "walking":
        return route.distance_m
    if route.mode == "transit":
        return route.walking_distance_m
    return None


def route_problem(route: RoutePlan | None, mode: str, limit: int | None = None) -> str | None:
    """检查所选交通方式和可选上限；未设上限时不推测距离限制。"""
    if route is None:
        return "相邻景点路线缺失"
    allowed = {"walking", "transit"} if mode == "auto" else {mode}
    if route.mode not in allowed:
        return f"路线交通方式{route.mode}不符合所选方式{mode}"
    if limit is not None:
        distance = walking_distance(route)
        if distance is None:
            return "路线没有可验证的步行距离"
        if distance > limit:
            return f"路线步行{distance}米，超过{limit}米上限"
    return None
