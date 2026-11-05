"""独立检查餐饮候选的覆盖、当前锚点和说明，不把候选当已核实菜单。"""

from math import asin, cos, radians, sin, sqrt


def evaluate_dining_resolution(request, plan):
    if all(day.dining_resolution is None for day in plan.days):
        return {"status": "not_evaluated", "details": ["Legacy response has no dining delivery report."]}
    aliases = {
        "公共交通": "transit", "公交": "transit", "地铁": "transit", "公交地铁": "transit", "transit": "transit",
        "自动": "auto", "自动选择": "auto", "智能推荐": "auto", "auto": "auto",
        "步行": "walking", "walking": "walking",
        "自驾": "driving", "驾车": "driving", "开车": "driving", "打车": "driving", "出租车": "driving", "driving": "driving",
    }
    mode = request.route_mode or aliases.get(request.transportation.strip().lower())
    failures = []
    for day in plan.days:
        report = day.dining_resolution
        if report is None:
            failures.append(f"Day {day.day_index} has no dining report.")
            continue
        expected = 2 if day.attractions else 0
        anchors = {"lunch": day.attractions[0], "dinner": day.attractions[-1]} if day.attractions else {}
        slots = [m.type for m in day.meals]
        if len(slots) != len(set(slots)) or any(slot not in anchors for slot in slots):
            failures.append("Duplicate or unrelated meal slot.")
        provided = len(set(slots) & set(anchors))
        status = "not_applicable" if not expected else "candidates_available" if provided == 2 else "partial" if provided else "needs_choice"
        if (report.required_slots, report.provided_slots, report.status) != (expected, provided, status):
            failures.append("Dining slot count/status does not match delivered candidates.")
        extra = request.free_text_input or ""
        for phrase in ("没有过敏史", "没有过敏", "不过敏", "无过敏", "没有忌口", "无忌口", "不需要忌口"):
            extra = extra.replace(phrase, " ")
        from_extra = any(word in extra for word in ("清淡", "少油", "少盐", "不要辣", "不辣", "微辣", "素食", "纯素", "清真", "无麸质", "过敏", "忌口", "不吃", "不能吃", "本地菜", "当地菜", "本地餐饮", "当地餐饮", "本地美食", "当地美食", "本地小吃", "当地小吃"))
        verification = "not_verified" if request.dining_preference or from_extra else "not_requested"
        if (report.requested_preference, report.preference_verification) != (request.dining_preference, verification):
            failures.append("Original dining preference was changed or falsely verified.")
        route_map = {(r.origin.id, r.destination.id): r for r in day.routes}
        pending = False
        for a, b in zip(day.attractions, day.attractions[1:]):
            r = route_map.get((a.poi_id, b.poi_id))
            allowed = {"walking", "transit"} if mode == "auto" else {mode} if mode else set()
            walking = r.distance_m if r and r.mode == "walking" else r.walking_distance_m if r and r.mode == "transit" else None
            if r is None or r.mode not in allowed or (request.max_leg_walking_m is not None and (walking is None or walking > request.max_leg_walking_m)):
                pending = True
        if report.based_on_provisional_itinerary != pending:
            failures.append("Dining report misstates whether adjacent routes are pending.")
        for meal in day.meals:
            anchor = anchors.get(meal.type)
            if anchor is None or meal.anchor_poi_id != anchor.poi_id or anchor.location is None or meal.location is None:
                failures.append("Meal is not anchored to its current attraction with known coordinates.")
                continue
            la, lb = radians(anchor.location.latitude), radians(meal.location.latitude)
            da = radians(meal.location.latitude - anchor.location.latitude)
            db = radians(meal.location.longitude - anchor.location.longitude)
            value = sin(da / 2) ** 2 + cos(la) * cos(lb) * sin(db / 2) ** 2
            distance = 6371000 * 2 * asin(sqrt(max(0, min(1, value))))
            if distance > 2000 or meal.straight_line_distance_m is None or abs(round(distance) - meal.straight_line_distance_m) > 1:
                failures.append("Meal radius/distance does not match independently calculated straight-line distance.")
        missing = set(anchors) - set(slots)
        unresolved = [x.meal_type for x in report.unresolved]
        if len(unresolved) != len(set(unresolved)) or set(unresolved) != missing:
            failures.append("Unresolved dining slots are missing, duplicated or stale.")
        diagnostics = {(d.meal_type, d.anchor_poi_id): d for d in plan.dining_diagnostics if d.day_index == day.day_index}
        for item in report.unresolved:
            anchor = anchors.get(item.meal_type)
            diagnostic = diagnostics.get((item.meal_type, item.anchor_poi_id))
            if anchor is None or item.anchor_poi_id != anchor.poi_id or item.anchor_name != anchor.name or diagnostic is None or item.reason != diagnostic.status:
                failures.append("Dining failure explanation is not supported by current anchor/diagnostic.")
        if missing and not report.alternatives:
            failures.append("Unfilled dining slots have no alternatives.")
        if not missing and report.alternatives and (not report.preference_notes or any(x.kind != "contact_restaurant" for x in report.alternatives)):
            failures.append("Completed dining slots have unrelated alternatives.")
        for option in report.alternatives:
            if option.verification != "not_verified" or not option.requires_confirmation:
                failures.append("Dining alternative was applied or falsely verified.")
    return {"status": "failed" if failures else "passed", "details": failures}
