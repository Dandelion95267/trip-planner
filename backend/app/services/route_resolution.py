"""有限搜索未找到完整路线时，如实交付部分结果和待用户选择的调整方向。"""

from app.models.schemas import RouteAlternative, RouteResolution, TripPlan, TripRequest, UnresolvedRoute
from app.services.route_policy import resolve_route_mode, route_problem, walking_distance
from app.services.route_search import MESSAGES


REASONS = {
    **MESSAGES,
    "not_queried": "本段尚未完成查询，不能判断是否存在合格路线。",
    "missing_poi_id": "起终点缺少地点ID，本段尚未完成路线查询。",
    "mode_mismatch": "已有路线的交通方式不符合原要求，不能算作已核验通过。",
    "unsupported_mode": "当前交通方式尚不支持路线查询，可在需求中选择步行、公交、自动或驾车。",
}


def build_route_resolution(request: TripRequest, plan: TripPlan) -> RouteResolution:
    mode = resolve_route_mode(request.transportation, request.route_mode)
    limit = request.max_leg_walking_m
    total = verified = 0
    unresolved = []
    # 只解释当前方案、当前模式及当前上限对应的最后一次诊断，不挪用旧路段。
    diagnostics = {
        (d.origin_poi_id, d.destination_poi_id): d for d in plan.route_diagnostics
        if d.mode == mode and d.max_leg_walking_m == limit
    }
    for day in plan.days:
        routes = {(r.origin.id, r.destination.id): r for r in day.routes}
        for a, b in zip(day.attractions, day.attractions[1:]):
            total += 1
            route = routes.get((a.poi_id, b.poi_id))
            problem = route_problem(route, mode, limit) if mode is not None else REASONS["unsupported_mode"]
            if not problem and a.poi_id and b.poi_id:
                verified += 1
                continue
            diagnostic = diagnostics.get((a.poi_id, b.poi_id))
            reason, observed = "not_queried", None
            if not a.poi_id or not b.poi_id:
                reason = "missing_poi_id"
            elif mode is None:
                reason = "unsupported_mode"
            elif route is not None:
                if (mode != "auto" and route.mode != mode) or (mode == "auto" and route.mode not in {"walking", "transit"}):
                    reason = "mode_mismatch"
                else:
                    observed = walking_distance(route)
                    reason = "walking_unknown" if observed is None else "over_limit"
            elif diagnostic is not None and diagnostic.status != "available":
                reason = diagnostic.status
                observed = diagnostic.minimum_returned_walking_m
            explanation = REASONS[reason]
            if reason == "over_limit" and observed is not None:
                explanation = f"本次已返回方案中已知最小步行距离为{observed}米，超过原上限{limit}米；未取得符合原要求的方案。"
            elif reason == "no_route":
                explanation += "这不证明该城市或所有候选组合都没有可行路线。"
            unresolved.append(UnresolvedRoute(
                day_index=day.day_index, origin_poi_id=a.poi_id, destination_poi_id=b.poi_id,
                origin_name=a.name, destination_name=b.name, reason=reason,
                explanation=explanation, observed_walking_m=observed,
            ))

    alternatives = []
    if unresolved:
        alternatives = [
            RouteAlternative(kind="retry", title="保留原条件，重新查询",
                             description="保持交通方式、步行上限和景点数量要求，稍后重新查询。地图返回可能变化，也可能仍未找到完整方案。"),
            RouteAlternative(kind="change_attractions", title="保留交通限制，调整景点或顺序",
                             description="保持原交通方式、步行上限和每日景点数量要求，调整未核验路段关联的景点或顺序后重新规划。已有预约请在其他需求中注明。",
                             condition_changes=["需要用户选择调整景点或顺序；已有首站约束仍保留"]),
        ]
        if limit is not None and limit < 50000:
            observed = [x.observed_walking_m for x in unresolved
                        if x.reason == "over_limit" and x.observed_walking_m is not None and limit < x.observed_walking_m <= 50000]
            suggested = max(observed) if observed else None
            description = "仅在体力允许且你愿意时提高步行上限，再重新查询；景点内部、酒店和餐馆接驳仍需另行确认。"
            if suggested is not None:
                description = f"未核验路段的已返回方案中出现了{suggested}米的步行距离。可考虑将上限从{limit}米调至{suggested}米后重新查询；这不保证整趟行程可行。"
            alternatives.append(RouteAlternative(
                kind="relax_walking_limit", title="自行决定是否提高步行上限", description=description,
                condition_changes=[f"需要你同意放宽原{limit}米上限"], suggested_max_leg_walking_m=suggested,
            ))
        if mode != "driving":
            alternatives.append(RouteAlternative(
                kind="change_transport", title="自行决定是否改用驾车或打车接驳",
                description="仅在愿意改变原交通要求时考虑；替代路线、费用、停车及上下车步行均未查询。驾车模式无法核验原有的完整步行接驳上限。",
                condition_changes=["需要你同意改变原交通要求", "改用驾车选项时需自行清空步行上限；这不代表符合原步行要求"],
            ))
        summary = f"按原条件已核验{verified}/{total}段相邻景点路线；其余路段在本次候选与查询预算内未找到合格方案或未完成核验。请保留已验证部分，并选择下一步。"
    elif total:
        summary = f"按原交通方式和可选步行上限，已核验{verified}/{total}段相邻景点路线。"
    else:
        summary = "当前没有相邻景点路段可供核验，不能据此声称完整出行满足路线要求。"
    return RouteResolution(
        status="needs_choice" if unresolved else "verified" if total else "not_applicable",
        requested_transportation=request.transportation, requested_mode=mode, max_leg_walking_m=limit,
        preserve_first_attraction=request.preserve_first_attraction,
        required_segments=total, verified_segments=verified, summary=summary,
        limitations=["只核验相邻景点路线，不含景点内部、酒店或餐馆接驳。",
                     "查询受候选范围与时间预算限制，暂未找到不等于已证明不存在可行方案。",
                     "替代选择尚未重新查询或应用；需要你确认条件后重新规划。"] if unresolved else ["只核验相邻景点路线，不含景点内部、酒店或餐馆接驳，实际出行前仍需确认。"],
        unresolved=unresolved, alternatives=alternatives,
    )
