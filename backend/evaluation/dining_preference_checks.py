"""独立核对偏好证据是否来自同次地图字段，不评价尚未提供的菜单事实。"""


ALIASES = {
    "川菜": ("川菜", "四川菜"), "湘菜": ("湘菜", "湖南菜"), "粤菜": ("粤菜", "广东菜"),
    "东北菜": ("东北菜",), "京菜": ("京菜", "北京菜"), "杭帮菜": ("杭帮菜",),
    "越南菜": ("越南菜",), "泰国菜": ("泰国菜", "泰餐"),
    "日本料理": ("日本料理", "日料", "日本菜"), "韩国料理": ("韩国料理", "韩餐", "韩国菜"),
    "西餐": ("西餐",), "面食": ("面食", "面馆"),
}
LOCAL = ("本地菜", "当地菜", "本地餐饮", "当地餐饮", "本地美食", "当地美食", "本地小吃", "当地小吃")


def evaluate_dining_preferences(request, plan):
    meals = [m for d in plan.days for m in d.meals]
    active = any(m.preference_level is not None for m in meals) or any(d.dining_resolution and d.dining_resolution.preference_notes for d in plan.days)
    if not active:
        return {"status": "not_evaluated", "details": ["Legacy response has no preference evidence contract."]}
    captured = plan.candidate_evidence
    if captured is None:
        return {"status": "unknown", "details": ["Same-run evidence is required to verify preference claims."]}
    observations = [p for s in captured.searches for p in s.pois] + [d.poi for d in captured.details]
    raw = request.dining_preference + " " + (request.free_text_input or "")
    failures = []
    for meal in meals:
        rows = [p for p in observations if p.id == meal.poi_id]
        types = {p.type for p in rows if p.type}
        names = {p.name for p in rows}
        if meal.poi_category and meal.poi_category not in types:
            failures.append("Meal category is not backed by a captured search/detail.")
        evidence = meal.preference_evidence
        if "清淡" in raw and not any(e.preference == "清淡" and e.kind == "taste" and e.status == "unknown" for e in evidence):
            failures.append("Requested light taste was omitted or falsely verified.")
        if any(t in raw for t in LOCAL) and not any(e.preference == "本地菜" and e.kind == "cuisine" for e in evidence):
            failures.append("Requested local cuisine criterion was omitted.")
        for item in evidence:
            if item.status == "conflict" or (item.strict and item.status != "supported"):
                failures.append("A conflicting or unsupported strict candidate was placed in the itinerary.")
            requested = item.preference in raw or any(t in raw for t in ALIASES.get(item.preference, ()))
            if item.preference == "本地菜":
                requested = any(t in raw for t in LOCAL)
                target = "川菜" if request.city.strip().removesuffix("市") == "成都" else None
                if item.target_category != target:
                    failures.append("Local cuisine target is outside the declared city configuration.")
            elif item.kind == "cuisine" and item.target_category != item.preference:
                failures.append("Explicit cuisine target does not match its criterion.")
            if not requested:
                failures.append("Preference evidence describes an unrequested criterion.")
            if item.kind != "cuisine":
                if item.status != "unknown" or item.source != "not_available" or item.value is not None:
                    failures.append("Map-only data cannot verify taste, dietary restrictions or other menu requirements.")
                continue
            aliases = ALIASES.get(item.target_category, ())
            if item.status == "supported":
                if item.source != "poi_type" or item.value != meal.poi_category or item.value not in types or not any(t in (item.value or "") for t in aliases):
                    failures.append("Supported cuisine is not backed by the quoted map category.")
            elif item.status == "hint":
                if item.source != "poi_name" or item.value not in names or not any(t in (item.value or "") for t in aliases):
                    failures.append("Name hint is not backed by a captured name.")
            elif item.status == "unknown" and (item.source != "not_available" or item.value is not None):
                failures.append("Unknown cuisine contains unsupported evidence fields.")
        statuses = {e.status for e in evidence}
        level = "category_supported" if "supported" in statuses else "name_hint" if "hint" in statuses else "unverified" if evidence else "not_requested"
        if meal.preference_level != level:
            failures.append("Preference level does not match individual evidence statuses.")
    return {"status": "failed" if failures else "passed", "details": failures}
