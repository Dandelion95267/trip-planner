"""独立检查有限搜索后的交付说明，不参与原hard_status，也不调用生产决策函数。"""

from app.models.schemas import TripPlan, TripRequest


def evaluate_route_resolution(request: TripRequest, plan: TripPlan) -> dict:
    report = plan.route_resolution
    if report is None:
        return {"status": "not_evaluated", "details": ["Legacy response has no route resolution report."]}
    aliases = {
        "自动": "auto", "自动选择": "auto", "智能推荐": "auto", "auto": "auto",
        "公共交通": "transit", "公交": "transit", "地铁": "transit", "公交地铁": "transit", "transit": "transit",
        "步行": "walking", "walking": "walking",
        "自驾": "driving", "驾车": "driving", "开车": "driving", "打车": "driving", "出租车": "driving", "driving": "driving",
    }
    mode = request.route_mode or aliases.get(request.transportation.strip().lower())
    limit = request.max_leg_walking_m
    failures = []
    if (report.requested_transportation, report.requested_mode, report.max_leg_walking_m, report.preserve_first_attraction) != (
        request.transportation, mode, limit, request.preserve_first_attraction,
    ):
        failures.append("Report changed the original route constraints.")
    expected, verified, missing = 0, 0, set()
    observed = {}
    diagnostics = {(d.origin_poi_id, d.destination_poi_id): d for d in plan.route_diagnostics
                   if d.mode == mode and d.max_leg_walking_m == limit}
    reasons = {}
    for day in plan.days:
        routes = {(r.origin.id, r.destination.id): r for r in day.routes}
        for a, b in zip(day.attractions, day.attractions[1:]):
            expected += 1
            key = day.day_index, a.poi_id, b.poi_id
            route = routes.get((a.poi_id, b.poi_id))
            allowed = {"walking", "transit"} if mode == "auto" else {mode} if mode else set()
            distance = route.distance_m if route and route.mode == "walking" else route.walking_distance_m if route and route.mode == "transit" else None
            if a.poi_id and b.poi_id and route and route.mode in allowed and (limit is None or (distance is not None and distance <= limit)):
                verified += 1
                continue
            missing.add(key)
            reason, value = "not_queried", None
            if not a.poi_id or not b.poi_id:
                reason = "missing_poi_id"
            elif mode is None:
                reason = "unsupported_mode"
            elif route:
                if route.mode not in allowed:
                    reason = "mode_mismatch"
                else:
                    reason, value = ("walking_unknown" if distance is None else "over_limit"), distance
            else:
                diagnostic = diagnostics.get((a.poi_id, b.poi_id))
                if diagnostic and diagnostic.status != "available":
                    reason, value = diagnostic.status, diagnostic.minimum_returned_walking_m
            reasons[key], observed[key] = reason, value
    if (report.required_segments, report.verified_segments) != (expected, verified):
        failures.append("Reported coverage does not match routes satisfying the original constraints.")
    keys = [(x.day_index, x.origin_poi_id, x.destination_poi_id) for x in report.unresolved]
    if len(keys) != len(set(keys)) or set(keys) != missing:
        failures.append("Unresolved pair list is missing, duplicated or stale.")
    for item in report.unresolved:
        key = item.day_index, item.origin_poi_id, item.destination_poi_id
        if item.reason != reasons.get(key) or item.observed_walking_m != observed.get(key):
            failures.append("Unresolved reason/distance is not backed by current matching evidence.")
        if not item.explanation.strip():
            failures.append("Unresolved segment has no explanation.")
    status = "needs_choice" if missing else "verified" if expected else "not_applicable"
    if report.status != status:
        failures.append("Report status does not match the independently checked route outcome.")
    if missing:
        if not report.alternatives or not report.limitations or not report.summary.strip():
            failures.append("Missing explanation, search limitations or alternatives.")
        if plan.review and plan.review.status == "passed":
            failures.append("Incomplete routes were reported as a passed review.")
    for option in report.alternatives:
        if not option.requires_confirmation or option.verification != "not_verified":
            failures.append("An alternative was silently applied or falsely marked verified.")
        suggestion = option.suggested_max_leg_walking_m
        values = [value for key, value in observed.items() if reasons[key] == "over_limit"
                  and value is not None and limit is not None and limit < value <= 50000]
        if option.kind == "relax_walking_limit" and limit is None:
            failures.append("A walking limit relaxation was suggested without an original numeric limit.")
        if suggestion is not None and (option.kind != "relax_walking_limit" or not values or suggestion != max(values)):
            failures.append("Numeric alternative is not supported by observed over-limit results.")
    return {"status": "failed" if failures else "passed", "details": failures}
