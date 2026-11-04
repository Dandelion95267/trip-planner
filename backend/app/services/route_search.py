"""本次规划的路线结果与受限路径搜索，不请求外部服务。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.schemas import RouteDiagnostic, RoutePlan
from app.services.route_policy import walking_distance


RouteKey = tuple[str, str, str, str, int | None]
TRANSIENT_STATUSES = {"provider_error", "invalid_response", "timeout", "budget_exhausted"}
MESSAGES = {
    "available": "返回了可核验且符合当前要求的路线。",
    "over_limit": "本次返回的已知步行方案均超过上限。",
    "walking_unknown": "没有取得可验证且符合上限的步行距离。",
    "no_route": "本次查询未返回可用路线。",
    "missing_coordinates": "起终点缺少可用坐标。",
    "provider_error": "地图服务调用失败，保留预算内重试机会。",
    "invalid_response": "路线响应结构或字段无法解析。",
    "timeout": "本次路线查询等待超时。",
    "budget_exhausted": "本轮路线时间预算用尽，该路段未完成查询。",
}


@dataclass
class RouteOutcome:
    status: str
    route: RoutePlan | None = None
    minimum_walking_m: int | None = None
    attempts: int = 0
    generation: int = 0


@dataclass
class RouteSearchState:
    outcomes: dict[RouteKey, RouteOutcome] = field(default_factory=dict)
    diagnostics: list[RouteDiagnostic] = field(default_factory=list)
    generation: int = 0

    def begin_generation(self) -> int:
        self.generation += 1
        return self.generation

    def reusable(self, key: RouteKey) -> RouteOutcome | None:
        previous = self.outcomes.get(key)
        if previous is None:
            return None
        if (previous.status in TRANSIENT_STATUSES and previous.generation < self.generation
                and previous.attempts < 2):
            return None
        return previous

    def record(self, key: RouteKey, outcome: RouteOutcome, cache_hit: bool) -> None:
        self.diagnostics.append(RouteDiagnostic(
            generation=self.generation, origin_poi_id=key[1], destination_poi_id=key[2],
            mode=key[3], max_leg_walking_m=key[4], status=outcome.status,
            cache_hit=cache_hit, attempt=outcome.attempts,
            walking_distance_m=walking_distance(outcome.route) if outcome.route is not None else None,
            minimum_returned_walking_m=outcome.minimum_walking_m,
            message=MESSAGES[outcome.status],
        ))

    def for_prompt(self) -> list[dict]:
        return [
            {"origin_poi_id": key[1], "destination_poi_id": key[2], "mode": key[3],
             "max_leg_walking_m": key[4], "status": outcome.status,
             "minimum_returned_walking_m": outcome.minimum_walking_m,
             "walking_distance_m": walking_distance(outcome.route) if outcome.route else None,
             "duration_s": outcome.route.duration_s if outcome.route else None,
             "meaning": MESSAGES[outcome.status]}
            for key, outcome in list(self.outcomes.items())[-40:]
        ]


def find_verified_path(nodes: list[str], edges: dict[tuple[str, str], RoutePlan], size: int,
                       original: list[str], keep_first: bool = False,
                       avoid_ids: set[str] | None = None, expansion_limit: int = 2000) -> list[str] | None:
    """只沿已验证有向边找固定长度简单路径，限制扩展数，优先保留原景点。"""
    if size < 2 or size > len(nodes):
        return None
    avoided = avoid_ids or set()
    originals = set(original)
    expanded = 0

    def rank(node):
        return (node in avoided, node not in originals, nodes.index(node))

    def visit(path):
        nonlocal expanded
        expanded += 1
        if expanded > expansion_limit:
            return None
        if len(path) == size:
            return path
        next_nodes = sorted((node for node in nodes if node not in path and (path[-1], node) in edges), key=rank)
        for node in next_nodes:
            result = visit(path + [node])
            if result:
                return result
        return None

    starts = [original[0]] if keep_first and original else sorted(nodes, key=rank)
    for node in starts:
        if expanded > expansion_limit:
            break
        result = visit([node])
        if result:
            return result
    return None
