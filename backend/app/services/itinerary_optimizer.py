"""固定首个景点的直线距离启发式排序，不代表实际道路的最优路线。"""

from math import asin, cos, radians, sin, sqrt

from app.models.schemas import Location


def _distance(a: Location, b: Location) -> float:
    """用球面距离估算两点直线距离，单位为米。"""
    lat_a, lat_b = radians(a.latitude), radians(b.latitude)
    delta_lat = lat_b - lat_a
    delta_lon = radians(b.longitude - a.longitude)
    value = sin(delta_lat / 2) ** 2 + cos(lat_a) * cos(lat_b) * sin(delta_lon / 2) ** 2
    return 2 * 6371000 * asin(sqrt(max(0, min(1, value))))


def optimize_visit_order(locations: list[Location]) -> tuple[list[int], float, float]:
    """返回索引顺序与前后估算距离；保留所有点和首点，只接受更短的结果。"""
    original = list(range(len(locations)))
    if not locations:
        return original, 0.0, 0.0
    distances = [[_distance(a, b) for b in locations] for a in locations]

    def length(order):
        return sum(distances[a][b] for a, b in zip(order, order[1:]))

    before = length(original)
    order = [0]
    remaining = set(original[1:])
    # 先选择离当前点最近的下一站，相同距离时保持原索引的先后。
    while remaining:
        next_index = min(remaining, key=lambda index: (distances[order[-1]][index], index))
        order.append(next_index)
        remaining.remove(next_index)
    best_length = length(order)
    # 再尝试反转局部片段减少折返，首个景点不参与反转。
    for _ in range(len(locations)):
        improved = False
        for start in range(1, len(order) - 1):
            for end in range(start + 1, len(order)):
                candidate = order[:start] + list(reversed(order[start:end + 1])) + order[end + 1:]
                candidate_length = length(candidate)
                if candidate_length + 0.001 < best_length:
                    order, best_length = candidate, candidate_length
                    improved = True
                    break
            if improved:
                break
        if not improved:
            break
    # 不为不足一米的差别调整顺序，也不接受比原行程更长的候选结果。
    if best_length + 1 < before:
        return order, before, best_length
    return original, before, before
