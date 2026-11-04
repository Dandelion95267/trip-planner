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
