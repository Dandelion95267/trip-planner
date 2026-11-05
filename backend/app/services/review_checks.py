"""审核的确定性依据：整数秒汇总，保守核对同日同餐文字，不推断接驳。"""

from difflib import SequenceMatcher
import re
import unicodedata

from app.models.schemas import MealConsistencyCheck, ReviewTimeEvidence, TripPlan, TripReviewIssue, TripReviewResult


MEAL_NAMES = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "snack": "加餐"}
PROTECTED_REVIEW_FIELDS = ("time_evidence", "meal_consistency", "semantic_status")


def duration_text(seconds: int) -> str:
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes}分钟" if remainder == 0 else f"{minutes}分{remainder}秒"


def build_time_evidence(plan: TripPlan) -> list[ReviewTimeEvidence]:
    result = []
    for day in plan.days:
        routes = {(r.origin.id, r.destination.id): r for r in day.routes}
        pairs = [(a.poi_id, b.poi_id) for a, b in zip(day.attractions, day.attractions[1:])]
        known = [routes[p] for p in pairs if all(p) and p in routes]
        visit_minutes = sum(a.visit_duration for a in day.attractions)
        route_seconds = sum(r.duration_s for r in known)
        total_seconds = visit_minutes * 60 + route_seconds
        complete = len(known) == len(pairs)
        coverage = "相邻景点路线已齐" if complete else f"相邻景点路线仅有{len(known)}/{len(pairs)}段，缺失耗时未知"
        result.append(ReviewTimeEvidence(
            day_index=day.day_index, suggested_visit_minutes=visit_minutes,
            expected_route_count=len(pairs), known_route_count=len(known),
            known_route_duration_s=route_seconds, known_total_duration_s=total_seconds,
            all_adjacent_routes_available=complete,
            display_text=(f"第{day.day_index + 1}天：建议游览{visit_minutes}分钟＋已知相邻景点交通{duration_text(route_seconds)}，"
                          f"已知合计{duration_text(total_seconds)}；{coverage}。"
                          "此合计不是完整全天耗时，未计入用餐、休息及酒店/餐馆接驳；景点内部步行未单独核验。"),
        ))
    return result


def normalize_name(value: str) -> str:
    return re.sub(r"[\s·•‘’“”\"'()（）\-—]", "", unicodedata.normalize("NFKC", value)).casefold()


def _mentions(text: str, slot: str) -> list[tuple[str, str]]:
    """只识别明确的命名格式；自由用餐、否定/备选/多名称不强行解释。"""
    found = []
    for clause in re.split(r"[，,。；;\n]", text):
        if slot not in clause or any(w in clause for w in ("不去", "不在", "不要", "取消", "备选", "或者", "或去", "改为", "例如", "自行", "待定",
                "如果", "若", "否则", "可以", "可考虑", "也可", "原计划", "原先", "之前")):
            continue
        patterns = [
            rf"{slot}(?:候选|安排|地点|餐馆)?\s*[:：]\s*[‘“\"]?(.+)$",
            rf"{slot}(?:安排在|安排|前往|选在|去|在|到)\s*[‘“\"]?(.+?)(?:用餐|就餐|吃饭|$)",
            rf"(?:在|到|去|前往)(.+?)(?:享用|用)?{slot}(?:后|$)",
        ]
        match = next((m for pattern in patterns if (m := re.search(pattern, clause))), None)
        if match is None:
            continue
        name = match.group(1).strip().strip('‘’“”"')
        # “在某景点附近的餐馆X午餐”只取用餐场所，保留完整店名/分店名。
        name = re.split(r"(?:附近|周边)的", name)[-1].strip()
        if not 1 < len(name) <= 100 or any(w in name for w in ("之间", "休息", "景点附近", "自由", "未安排", "或", "可选")):
            continue
        found.append((name, clause.strip()))
    return list(dict.fromkeys(found))


def _stem(name: str) -> str:
    name = normalize_name(name)
    for word in ("餐馆", "菜馆", "餐厅", "饭店", "酒楼", "饭馆", "食堂", "分店", "旗舰店"):
        name = name.replace(word, "")
    return name


def check_meal_names(plan: TripPlan) -> tuple[list[MealConsistencyCheck], list[TripReviewIssue]]:
    observed = {}
    capture = plan.candidate_evidence
    pois = [] if capture is None else (capture.candidates + capture.hotel_candidates + capture.restaurant_candidates
        + [p for search in capture.searches for p in search.pois] + [d.poi for d in capture.details])
    for poi in pois:
        observed.setdefault(normalize_name(poi.name), set()).add(poi.id)
    for day in plan.days:
        for meal in day.meals:
            if meal.poi_id:
                observed.setdefault(normalize_name(meal.name), set()).add(meal.poi_id)
    checks, issues = [], []
    for day in plan.days:
        for meal in day.meals:
            slot = MEAL_NAMES[meal.type]
            candidates = _mentions(day.description, slot)
            check = MealConsistencyCheck(day_index=day.day_index, meal_type=meal.type,
                expected_poi_id=meal.poi_id, expected_name=meal.name, status="unknown",
                explanation=f"{slot}：文字未提供可唯一识别的餐馆名称；未判为冲突，实际接驳仍待确认。")
            if len(candidates) == 1:
                name, excerpt = candidates[0]
                check.mentioned_name, check.source_excerpt = name, excerpt
                left, right = normalize_name(meal.name), normalize_name(name)
                ids = observed.get(right, set())
                exact = left == right or bool(meal.poi_id and ids == {meal.poi_id})
                distinct_id = bool(meal.poi_id and len(ids) == 1 and meal.poi_id not in ids)
                # 没有独立ID时只处理差异明确的完整店名。简称、共享品牌、相似字形保持未知。
                plausible_alias = (left in right or right in left or _stem(left) == _stem(right)
                    or bool(set(_stem(left)) & set(_stem(right)))
                    or SequenceMatcher(None, left, right).ratio() >= 0.85)
                named_place = any(w in name for w in ("餐馆", "菜馆", "餐厅", "饭店", "酒楼", "面馆", "火锅", "食堂"))
                if exact:
                    check.status = "matched"
                    check.explanation = f"{slot}：文字名称与列表名称或同次地点ID一致；不代表菜单、营业或接驳已确认。"
                elif distinct_id or (named_place and not plausible_alias):
                    check.status = "conflict"
                    check.explanation = f"{slot}：文字为“{name}”，列表为“{meal.name}”，名称不一致，需要统一或提供同一地点依据。"
                    issues.append(TripReviewIssue(category="consistency", day_index=day.day_index, source="program",
                        problem=f"{slot}介绍与餐馆列表的名称不一致。",
                        evidence=f"当天原文：“{excerpt}”；meals[{meal.type}].name=“{meal.name}”" + (f"，poi_id={meal.poi_id}" if meal.poi_id else "") + "。",
                        suggestion="核对原餐馆资料；若为同一地点请提供可核对的名称/ID依据，否则按当前meals的餐馆重写该时段介绍，不能只改变审核结论。"))
                else:
                    check.explanation = f"{slot}：名称可能为简称、别名或尚无法识别，未判为冲突；需进一步核对。"
            checks.append(check)
    return checks, issues


def compose_review(model_result, program_issues, times, meals, semantic_status):
    issues = list(program_issues)
    seen = {(i.category, i.day_index, i.problem, i.evidence) for i in issues}
    if model_result is not None:
        for issue in model_result.issues:
            key = (issue.category, issue.day_index, issue.problem, issue.evidence)
            if key not in seen:
                issues.append(issue.model_copy(update={"source": "model"}))
                seen.add(key)
    if semantic_status != "completed" and not issues:
        raise ValueError("语义审核未完成且无程序问题时，不能生成通过结论。")
    # 不公开模型自行生成的合计或其摘要；数值只来自time_evidence的程序显示文本。
    summary = f"本次列出{len(issues)}项待修改意见，请核对逐项依据。" if issues else "未发现有明确依据的待修改问题，未知信息仍需确认。"
    if semantic_status == "unavailable":
        summary += "语义审核未完成，已保留程序发现的问题。"
    elif semantic_status == "not_run":
        summary += "语义审核尚未执行。"
    return TripReviewResult(passed=not issues, summary=summary, issues=issues,
        time_evidence=times, meal_consistency=meals, semantic_status=semantic_status)
