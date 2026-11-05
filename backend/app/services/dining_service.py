"""围绕最终景点选择有坐标依据的餐馆；不推测道路距离或菜单。"""

import asyncio
from dataclasses import dataclass, field

from app.models.schemas import DiningAlternative, DiningDiagnostic, DiningResolution, Meal, POIInfo, TripPlan, TripRequest, UnresolvedMeal
from app.services.amap_service import AmapService, AmapServiceError
from app.services.itinerary_optimizer import _distance
from app.services.route_policy import resolve_route_mode, route_problem
from app.services.dining_preferences import assess, build_profile, preference_level, preference_summary, rank_key, rejection_reason


NEARBY_RADIUS_M = 2000
DINING_BUDGET_S = 35
MAX_SEARCHES_PER_REQUEST = 8
MAX_DETAILS_PER_REQUEST = 16
FAILURE_REASONS = {
    "no_coordinates": "当前参考景点没有取得可核验坐标，本时段尚未完成周边餐馆查询",
    "budget_exhausted": "本次餐饮查询的时间或次数预算已用尽，本时段未完成核验",
    "provider_error": "周边餐馆服务暂不可用，本时段查询未完成",
    "timeout": "本次周边餐馆查询等待超时，本时段尚未核验完成",
    "no_nearby_candidate": "本次查询没有取得直线半径内且坐标可核验的餐馆候选",
    "preference_conflict": "本次候选存在明确类别冲突，未取得可用于原偏好的位置可核验候选",
    "preference_unverified": "缺少确认严格餐饮要求的依据，未将未经核实的餐馆列入安排",
}


@dataclass
class DiningState:
    cache: dict[tuple, list[POIInfo]] = field(default_factory=dict)
    searches: int = 0
    details: int = 0
    generation: int = 0
    diagnostics: list[DiningDiagnostic] = field(default_factory=list)
    relaxed_keys: set[tuple] = field(default_factory=set)


class DiningService:
    def __init__(self, maps: AmapService):
        self.maps = maps

    async def finalize(self, plan: TripPlan, request: TripRequest, state: DiningState) -> None:
        state.generation += 1
        # 路线未完成时仍围绕当前景点查餐馆；景点变更由锚点缓存键触发重新查询。
        loop = asyncio.get_running_loop()
        deadline = loop.time() + DINING_BUDGET_S
        detail_failures = {}
        profile = build_profile(request)

        async def detail(poi_id):
            cached = self.maps.source_evidence.details.get(poi_id)
            if cached is not None:
                return cached.poi.model_copy(deep=True)
            if not poi_id or state.details >= MAX_DETAILS_PER_REQUEST or loop.time() >= deadline:
                detail_failures[poi_id] = "no_coordinates" if not poi_id else "budget_exhausted"
                return None
            state.details += 1
            try:
                async with asyncio.timeout(min(6, max(.001, deadline - loop.time()))):
                    return await self.maps.get_poi_detail(poi_id)
            except AmapServiceError:
                detail_failures[poi_id] = "provider_error"
                return None
            except TimeoutError:
                detail_failures[poi_id] = "timeout"
                return None

        for day in plan.days:
            day.meals = []
            day.dining_warnings = []
            provisional = not self._day_routes_ready(day, request)
            if not day.attractions:
                day.dining_resolution = DiningResolution(
                    status="not_applicable", requested_preference=request.dining_preference,
                    preference_verification="not_verified" if profile.criteria else "not_requested",
                    based_on_provisional_itinerary=provisional, required_slots=0, provided_slots=0,
                    summary="当前没有可供参考的景点，尚未选择用餐位置。", preference_notes=list(profile.notes),
                )
                continue
            if provisional:
                day.dining_warnings.append("景点间路线尚未核验完整；以下餐馆仅围绕当前暂定景点筛选。调整景点后需重新查询，餐馆接驳未核验，也不代表景点间交通要求已满足。")
            selected_ids = set()
            unresolved = []
            for slot, anchor in [("lunch", day.attractions[0]), ("dinner", day.attractions[-1])]:
                entry = DiningDiagnostic(generation=state.generation, day_index=day.day_index,
                                         meal_type=slot, anchor_poi_id=anchor.poi_id, status="no_coordinates")
                if anchor.location is None:
                    found = await detail(anchor.poi_id)
                    if found is not None:
                        anchor.location = found.location
                    if anchor.location is None:
                        entry.status = detail_failures.get(anchor.poi_id, "no_coordinates")
                if anchor.location is not None:
                    key = (anchor.poi_id, anchor.location.longitude, anchor.location.latitude,
                           request.city, request.dining_preference, profile.signature, NEARBY_RADIUS_M)
                    entry.cache_hit = key in state.cache
                    candidates = state.cache.get(key)
                    if candidates is None:
                        if state.searches >= MAX_SEARCHES_PER_REQUEST or loop.time() >= deadline:
                            entry.status = "budget_exhausted"
                        else:
                            state.searches += 1
                            try:
                                async with asyncio.timeout(min(8, max(.001, deadline - loop.time()))):
                                    candidates = await self.maps.search_restaurants_near(
                                        anchor.location, profile.queries[0], NEARBY_RADIUS_M,
                                    )
                                state.cache[key] = [p.model_copy(deep=True) for p in candidates[:10]]
                                candidates = state.cache[key]
                            except AmapServiceError:
                                entry.status = "provider_error"
                            except TimeoutError:
                                entry.status = "timeout"
                    eligible = {}
                    rejected = {}
                    detail_count = 0

                    async def prepare(items, detail_allowance):
                        nonlocal detail_count
                        indexed = list(enumerate(items))
                        indexed.sort(key=lambda pair: (*rank_key(assess(profile, pair[1])), pair[0]))
                        for _, candidate in indexed:
                            eligible.pop(candidate.id, None)
                            evidence = assess(profile, candidate)
                            reason = rejection_reason(evidence)
                            if reason == "preference_conflict" or any(e.strict and e.kind != "cuisine" and e.status != "supported" for e in evidence):
                                rejected[candidate.id] = reason
                                continue
                            cached = self.maps.source_evidence.details.get(candidate.id)
                            found = cached.poi if cached is not None else None
                            needs_category = any(e.kind == "cuisine" and e.target_category and e.status != "supported" for e in evidence)
                            if found is None and (candidate.location is None or needs_category) and detail_count < detail_allowance:
                                detail_count += 1
                                found = await detail(candidate.id)
                            if found is not None:
                                candidate.location = candidate.location or found.location
                                candidate.type = found.type or candidate.type
                            canonical = self.maps.source_evidence.groups["restaurant_candidates"].get(candidate.id, candidate)
                            candidate.name, candidate.address = canonical.name, canonical.address
                            evidence = assess(profile, candidate)
                            reason = rejection_reason(evidence)
                            if reason:
                                rejected[candidate.id] = reason
                                continue
                            rejected.pop(candidate.id, None)
                            if candidate.location is not None:
                                distance = _distance(anchor.location, candidate.location)
                                if distance <= NEARBY_RADIUS_M:
                                    eligible[candidate.id] = (candidate, evidence, distance)

                    if candidates is not None:
                        # 无明显类别线索时先留一次详情机会给拆分查询，单时段总共至多补两条。
                        category_requested = any(c.kind == "cuisine" for c in profile.criteria)
                        uncertain = not any(any(e.status in {"supported", "hint"} for e in assess(profile, c)) for c in candidates)
                        allowance = 1 if category_requested and uncertain and len(profile.queries) > 1 and key not in state.relaxed_keys else 2
                        await prepare(candidates, allowance)
                    needs_expansion = not eligible or (any(c.kind == "cuisine" for c in profile.criteria)
                        and not any(any(e.kind == "cuisine" and e.status == "supported" for e in v[1]) for v in eligible.values()))
                    # 拆分查询仍保留菜系条件；不再把本地菜检索直接降为任意餐馆。
                    if (candidates is not None and needs_expansion and len(profile.queries) > 1 and key not in state.relaxed_keys
                            and state.searches < MAX_SEARCHES_PER_REQUEST and loop.time() < deadline):
                        state.searches += 1
                        try:
                            async with asyncio.timeout(min(8, max(.001, deadline - loop.time()))):
                                extra = await self.maps.search_restaurants_near(anchor.location, profile.queries[1], NEARBY_RADIUS_M)
                            merged = {p.id: p for p in candidates}
                            for item in extra[:10]:
                                merged.setdefault(item.id, item)
                            candidates = list(merged.values())
                            state.cache[key] = candidates
                            state.relaxed_keys.add(key)
                            await prepare(extra[:10], 2)
                        except AmapServiceError:
                            entry.status = "provider_error"
                        except TimeoutError:
                            entry.status = "timeout"
                    entry.search_relaxed = key in state.relaxed_keys
                    entry.query_terms = list(profile.queries if entry.search_relaxed else profile.queries[:1])
                    entry.preference_rejections = len(rejected)
                    if entry.search_relaxed:
                        day.dining_warnings.append(
                            f"{'午餐' if slot == 'lunch' else '晚餐'}：完整关键词未取得充分候选或类别依据，已用“{profile.queries[1] or '餐馆'}”补充查询；原偏好未改变，清淡做法等偏好适配尚未核实。"
                        )
                    if candidates is not None:
                        if entry.status not in {"provider_error", "timeout"}:
                            entry.status = ("preference_unverified" if "preference_unverified" in rejected.values() else
                                            "preference_conflict" if rejected else "no_nearby_candidate")
                        if eligible:
                            candidate, evidence, distance = min(eligible.values(), key=lambda item: (*rank_key(item[1]), item[0].id in selected_ids, item[2], item[0].id))
                            # 名称地址采用原始搜索字段，坐标补全有独立详情证据。
                            canonical = self.maps.source_evidence.groups["restaurant_candidates"].get(candidate.id, candidate)
                            day.meals.append(Meal(
                                poi_id=candidate.id, type=slot, name=canonical.name, address=canonical.address,
                                location=candidate.location, anchor_poi_id=anchor.poi_id,
                                straight_line_distance_m=round(distance),
                                poi_category=candidate.type or None, preference_level=preference_level(evidence),
                                preference_evidence=evidence, preference_summary=preference_summary(evidence),
                                description=f"以{anchor.name}为参考筛选，估算直线距离约{round(distance)}米；不是步行距离。菜单、营业时间、饮食适配与实际接驳均未核实。" + ("这是当前暂定景点周边候选，景点变化后需重新查询。" if provisional else "") + (preference_summary(evidence) or ""),
                            ))
                            selected_ids.add(candidate.id)
                            entry.status, entry.selected_poi_id = "selected", candidate.id
                            entry.straight_line_distance_m = round(distance)
                if entry.status != "selected":
                    name = "午餐" if slot == "lunch" else "晚餐"
                    explanation = FAILURE_REASONS[entry.status] + "；不表示该区域没有餐馆。"
                    day.dining_warnings.append(f"{name}：{explanation}")
                    unresolved.append(UnresolvedMeal(
                        meal_type=slot, anchor_poi_id=anchor.poi_id, anchor_name=anchor.name,
                        reason=entry.status, explanation=explanation,
                    ))
                state.diagnostics.append(entry)
            choices = []
            if unresolved:
                choices = [
                    DiningAlternative(kind="retry", title="保留景点和餐饮要求，稍后重查", description="保持当前参考景点与原餐饮偏好重新查询；服务或预算限制消失后可能取得候选，也可能仍需确认。"),
                    DiningAlternative(kind="manual_search", title="围绕当前景点自行查找并联系餐馆", description="可在地图中围绕列出的参考景点查找餐馆，并联系商家确认菜单、营业、口味和接驳。这些结果尚未由本系统核验。"),
                ]
                if request.dining_preference and self._can_relax(request):
                    choices.append(DiningAlternative(kind="adjust_preferences", title="自行决定是否调整餐饮偏好", description="仅在愿意改变原偏好时，返回餐饮需求修改后重新规划；原偏好不会自动改写，新的匹配情况仍需核实。"))
            if profile.criteria and day.attractions:
                choices.append(DiningAlternative(kind="contact_restaurant", title="联系商家核实菜单与做法", description="地图类别只能支持类别检索方向，店名只是线索。请先联系商家确认清淡做法或严格饮食要求，再决定是否使用候选；系统尚未替你联系或确认。"))
            provided = len({m.type for m in day.meals if m.type in {"lunch", "dinner"}})
            day.dining_resolution = DiningResolution(
                status="candidates_available" if provided == 2 else "partial" if provided else "needs_choice",
                requested_preference=request.dining_preference,
                preference_verification="not_verified" if profile.criteria else "not_requested",
                based_on_provisional_itinerary=provisional, required_slots=2, provided_slots=provided,
                summary=f"当前提供{provided}/2个用餐时段的餐馆候选；候选位置基于{'暂定' if provisional else '当前'}景点。菜单、营业、偏好与实际接驳仍需确认。",
                unresolved=unresolved, alternatives=choices,
                preference_notes=list(profile.notes),
            )
            self._describe_day(day)
        plan.dining_diagnostics = [x.model_copy(deep=True) for x in state.diagnostics]
        self._describe_overall(plan, request)

    @staticmethod
    def _can_relax(request):
        return len(build_profile(request).queries) > 1

    @staticmethod
    def _day_routes_ready(day, request):
        mode = resolve_route_mode(request.transportation, request.route_mode)
        if mode is None:
            return len(day.attractions) < 2
        routes = {(r.origin.id, r.destination.id): r for r in day.routes}
        for a, b in zip(day.attractions, day.attractions[1:]):
            route = routes.get((a.poi_id, b.poi_id))
            if route_problem(route, mode, request.max_leg_walking_m) is not None:
                return False
        return True

    @staticmethod
    def _describe_day(day):
        first, *rest = day.attractions
        parts = [f"上午：{first.name}（建议{first.visit_duration}分钟）。"]
        lunch = next((m for m in day.meals if m.type == "lunch"), None)
        dinner = next((m for m in day.meals if m.type == "dinner"), None)
        if lunch:
            parts.append(f"午餐候选：{lunch.name}，地址：{lunch.address or '未提供'}。{lunch.description}")
        parts.append("午餐后建议预留约1小时休息；餐饮和接驳耗时未核验，请按实际条件调整。")
        if rest:
            parts.append("下午按顺序游览：" + " → ".join(f"{p.name}（建议{p.visit_duration}分钟）" for p in rest) + "。")
        else:
            parts.append("下午根据体力自由休息，不额外添加未列出的景点。")
        if dinner:
            parts.append(f"晚餐候选：{dinner.name}，地址：{dinner.address or '未提供'}。{dinner.description}")
        parts.append("晚间建议休息，酒店与餐馆接驳未核验。开放时间和预约请出行前确认。")
        parts.extend(day.dining_warnings)
        day.description = "\n".join(parts)

    @staticmethod
    def _describe_overall(plan, request):
        expected = sum(max(0, len(d.attractions) - 1) for d in plan.days)
        matched = sum(len(d.routes) for d in plan.days)
        parts = ["行程按当前景点和地图餐馆候选整理。游览时长、休息安排和交通耗时为参考，请确认开放与预约条件。",
                 f"相邻景点路线已取得{matched}/{expected}段；缺失路段不能视为满足交通要求。路线不含景点内部、酒店和餐馆接驳。",
                 "餐馆按参考景点周边检索并用坐标直线距离筛选；直线距离不是步行距离。菜单、营业时间、饮食要求是否满足和实际消费未确认。",
                 "候选住宿的房价、房态及接驳未核验。费用仅按用户填写标准另行估算。"]
        if request.max_leg_walking_m is not None:
            parts.append(f"相邻景点每段步行上限{request.max_leg_walking_m}米；只有已返回的合格路线完成核验，其他出行范围不能据此声称达标。")
        missing = [d.date for d in plan.days if d.date not in {w.date for w in plan.weather_info}]
        if missing:
            parts.append("暂无天气预报的日期：" + "、".join(missing) + "，请临近出行重新查询。")
        if any(d.dining_warnings for d in plan.days):
            parts.append("部分餐饮检索或偏好适配需要确认，请查看每日提示；没有列出候选的时段尚未完成安排。")
        if request.optimize_order:
            parts.append("景点顺序优化依据坐标直线距离，不保证实际道路最短或预约时间可行。")
        plan.overall_suggestions = "\n".join(parts)
