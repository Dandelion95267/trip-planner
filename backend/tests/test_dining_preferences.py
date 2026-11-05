"""偏好拆分、类别优先、严格条件与真实字段证据；不调用外部API。"""

import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from mcp.types import CallToolResult

from app.agents.trip_planner_agent import TripPlannerAgent
from app.models.schemas import POIInfo
from app.services.amap_service import AmapService
from app.services.dining_preferences import assess, build_profile, rejection_reason
from app.services.dining_service import DiningService, DiningState
from app.services.trip_workflow import TripPlanningWorkflow
from app.agents.review_agent import TripReviewAgent
import test_dining_service as fixtures
from test_walking_limits import plan, place, request


def make_request(text="本地菜 清淡", **updates):
    req, _ = fixtures.inputs()
    req.city, req.dining_preference = "成都", text
    for key, value in updates.items():
        setattr(req, key, value)
    return req


def maps_with(items, details=None, by_query=None):
    maps = AmapService()
    async def call(name, args):
        if name == "maps_around_search":
            values = by_query(args["keywords"]) if by_query else items
            return CallToolResult(content=[], structuredContent={"pois": values})
        if name == "maps_search_detail":
            data = (details or {}).get(args["id"]) or next(x for x in items if x["id"] == args["id"])
            return CallToolResult(content=[], structuredContent=data)
        raise AssertionError(name)
    maps._call_tool = AsyncMock(side_effect=call)
    return maps


class ProfileTests(unittest.TestCase):
    def test_local_cuisine_and_taste_are_separate_and_queries_keep_cuisine(self):
        p = build_profile(make_request())
        self.assertEqual([(x.label, x.kind, x.target) for x in p.criteria], [("本地菜", "cuisine", "川菜"), ("清淡", "taste", None)])
        self.assertEqual(p.queries, ("本地菜 清淡", "川菜"))

    def test_other_city_local_cuisine_is_not_guessed(self):
        p = build_profile(make_request(city="未知城市"))
        self.assertIsNone(p.criteria[0].target)
        evidence = assess(p, POIInfo(id="a", name="川菜馆", type="四川菜"))
        self.assertEqual(evidence[0].status, "unknown")

    def test_category_is_stronger_than_name_and_taste_is_always_unverified(self):
        p = build_profile(make_request())
        e = assess(p, POIInfo(id="a", name="清淡川菜馆", type="餐饮服务;四川菜(川菜)"))
        self.assertEqual([(x.status, x.source) for x in e], [("supported", "poi_type"), ("unknown", "not_available")])
        e = assess(p, POIInfo(id="a", name="川菜馆"))
        self.assertEqual(e[0].status, "hint")

    def test_foreign_or_conflicting_category_cannot_pass_as_local(self):
        p = build_profile(make_request())
        for name, category in [("越南菜馆", "外国餐厅;韩国料理"), ("川菜馆", "湖南菜(湘菜)"), ("越南菜馆", "四川菜(川菜)")]:
            with self.subTest(name=name, category=category):
                e = assess(p, POIInfo(id="a", name=name, type=category))
                self.assertEqual(rejection_reason(e), "preference_conflict")

    def test_strict_taste_and_dietary_constraints_need_evidence(self):
        for req in [make_request("必须清淡"), make_request("", free_text_input="不吃羊肉")]:
            p = build_profile(req)
            self.assertTrue(any(c.strict for c in p.criteria))
            self.assertEqual(len(p.queries), 1)
            self.assertEqual(rejection_reason(assess(p, POIInfo(id="a", name="普通餐馆"))), "preference_unverified")

    def test_route_must_does_not_make_soft_taste_a_strict_diet(self):
        p = build_profile(make_request("清淡", free_text_input="必须用公共交通，每天两个景点"))
        self.assertFalse(any(c.strict for c in p.criteria))
        self.assertEqual(len(p.queries), 2)

    def test_unknown_words_remain_unknown(self):
        p = build_profile(make_request("特殊口味"))
        self.assertEqual(assess(p, POIInfo(id="a", name="特殊口味餐馆"))[0].status, "unknown")

    def test_extra_food_restriction_at_sentence_end_is_preserved(self):
        p = build_profile(make_request("", free_text_input="同行者对花生过敏"))
        self.assertTrue(any(c.kind == "dietary" and c.strict for c in p.criteria))
        p = build_profile(make_request("", free_text_input="没有过敏史，无忌口"))
        self.assertEqual(p.criteria, ())

    def test_explicit_no_spicy_extra_request_is_not_assumed_verified(self):
        p = build_profile(make_request("", free_text_input="不要辣"))
        self.assertTrue(p.criteria[0].strict)
        self.assertEqual(rejection_reason(assess(p, POIInfo(id="a", name="不辣餐馆"))), "preference_unverified")


class PreferenceSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, maps, text="本地菜 清淡"):
        req, current = fixtures.inputs()
        req.city = current.city = "成都"
        req.dining_preference = text
        await DiningService(maps).finalize(current, req, DiningState())
        return req, current

    async def test_category_supported_farther_candidate_beats_nearby_unknown_and_conflict(self):
        maps = maps_with([
            {"id": "foreign", "name": "越南菜馆", "type": "外国餐厅", "location": "116.4001,39.9"},
            {"id": "unknown", "name": "普通餐馆", "type": "中餐厅", "location": "116.401,39.9"},
            {"id": "local", "name": "地图类别餐馆", "type": "四川菜(川菜)", "location": "116.415,39.9"},
        ])
        req, p = await self.run_case(maps)
        self.assertEqual([m.poi_id for m in p.days[0].meals], ["local", "local"])
        self.assertEqual(p.days[0].meals[0].preference_level, "category_supported")
        self.assertEqual(p.days[0].meals[0].preference_evidence[1].status, "unknown")
        self.assertEqual(req.dining_preference, "本地菜 清淡")
        self.assertEqual(p.days[0].dining_resolution.preference_verification, "not_verified")

    async def test_details_supply_category_even_if_search_already_has_coordinates(self):
        items = [{"id": "local", "name": "川菜候选", "location": "116.405,39.9"}]
        details = {"local": {**items[0], "type": "餐饮服务;中餐厅;四川菜(川菜)"}}
        maps = maps_with(items, details)
        _, p = await self.run_case(maps)
        self.assertEqual(p.days[0].meals[0].preference_evidence[0].source, "poi_type")
        self.assertIn("local", maps.source_evidence.details)
        self.assertEqual(sum(c.args[0] == "maps_search_detail" for c in maps._call_tool.await_args_list), 1)

    async def test_nonempty_conflicting_primary_query_triggers_cuisine_preserving_search(self):
        def values(query):
            if query == "川菜 餐馆":
                return [{"id": "good", "name": "川菜候选", "type": "四川菜", "location": "116.405,39.9"}]
            return [{"id": "bad", "name": "越南菜馆", "location": "116.405,39.9"}]
        maps = maps_with([], by_query=values)
        _, p = await self.run_case(maps)
        self.assertTrue(all(m.poi_id == "good" for m in p.days[0].meals))
        queries = [c.args[1]["keywords"] for c in maps._call_tool.await_args_list if c.args[0] == "maps_around_search"]
        self.assertEqual(queries, ["本地菜 清淡 餐馆", "川菜 餐馆"] * 2)

    async def test_no_evidence_candidate_is_labeled_unverified_not_supported(self):
        maps = maps_with([{"id": "u", "name": "资料缺失餐馆", "type": "中餐厅", "location": "116.405,39.9"}])
        _, p = await self.run_case(maps)
        self.assertEqual(p.days[0].meals[0].preference_level, "unverified")
        self.assertTrue(all(e.status == "unknown" for e in p.days[0].meals[0].preference_evidence))

    async def test_only_conflicting_results_are_not_filled_as_meals(self):
        maps = maps_with([{"id": "bad", "name": "越南菜馆", "type": "外国餐厅;越南菜", "location": "116.405,39.9"}])
        _, p = await self.run_case(maps)
        self.assertEqual(p.days[0].meals, [])
        self.assertTrue(all(x.reason == "preference_conflict" for x in p.days[0].dining_resolution.unresolved))

    async def test_strict_unverified_taste_does_not_select_general_restaurant(self):
        maps = maps_with([{"id": "u", "name": "清淡餐馆", "type": "中餐厅", "location": "116.405,39.9"}])
        _, p = await self.run_case(maps, "必须清淡")
        self.assertEqual(p.days[0].meals, [])
        self.assertTrue(all(x.reason == "preference_unverified" for x in p.days[0].dining_resolution.unresolved))
        self.assertNotIn("adjust_preferences", [x.kind for x in p.days[0].dining_resolution.alternatives])

    async def test_strict_cuisine_can_be_confirmed_by_detail_category_before_rejection(self):
        item = {"id": "r", "name": "川菜馆", "location": "116.405,39.9"}
        maps = maps_with([item], {"r": {**item, "type": "四川菜(川菜)"}})
        _, p = await self.run_case(maps, "必须川菜")
        self.assertEqual(len(p.days[0].meals), 2)
        self.assertEqual(p.days[0].meals[0].preference_evidence[0].status, "supported")

    async def test_changed_city_invalidates_local_cuisine_search_cache(self):
        maps = maps_with([{"id": "u", "name": "普通餐馆", "type": "中餐厅", "location": "116.405,39.9"}])
        req, p = fixtures.inputs()
        req.dining_preference = "本地菜 清淡"
        state = DiningState()
        await DiningService(maps).finalize(p, req, state)
        calls = maps._call_tool.await_count
        req.city = p.city = "成都"
        await DiningService(maps).finalize(p, req, state)
        self.assertGreater(maps._call_tool.await_count, calls)

    async def test_llm_cannot_forge_preference_evidence(self):
        payload = plan().model_dump()
        payload["days"][0]["meals"] = [{"poi_id": "R", "type": "lunch", "name": "R", "poi_category": "四川菜", "preference_evidence": {"forged": "supported"}, "preference_level": "category_supported"}]
        payload["days"][0]["meals"].append({**payload["days"][0]["meals"][0], "type": "dinner"})
        planner = TripPlannerAgent(llm=Mock(), amap_service=Mock())
        with patch("app.agents.trip_planner_agent.SimpleAgent") as model:
            model.return_value.run.return_value = json.dumps(payload)
            generated = planner._generate_plan(request(), [place("A"), place("B")], [], restaurant_candidates=[place("R")])
        self.assertEqual(generated.days[0].meals[0].preference_evidence, [])
        self.assertIsNone(generated.days[0].meals[0].preference_level)
        self.assertNotIn("DiningPreferenceEvidence", json.dumps(planner._model_schema()))

    async def test_unfilled_food_preferences_wait_for_choice_without_model_rewrites(self):
        maps = maps_with([{"id": "bad", "name": "越南菜馆", "type": "越南菜", "location": "116.405,39.9"}])
        req, p = await self.run_case(maps)
        req.enable_review = True
        planner = Mock(prepare_context=AsyncMock(return_value=object()), generate_plan=AsyncMock(return_value=p))
        reviewer = Mock(review_trip=AsyncMock())
        result = await TripPlanningWorkflow(planner, reviewer).plan_trip(req)
        self.assertEqual(result.review.status, "needs_revision")
        self.assertEqual(result.review.revisions, 0)
        planner.generate_plan.assert_awaited_once()
        reviewer.review_trip.assert_not_awaited()

    async def test_reviewer_receives_unknown_taste_as_data_with_contact_choice(self):
        maps = maps_with([{"id": "r", "name": "川菜馆", "type": "四川菜", "location": "116.405,39.9"}])
        req, p = await self.run_case(maps)
        with patch("app.agents.review_agent.SimpleAgent") as model:
            model.return_value.run.return_value = json.dumps({"passed": True, "summary": "无明确矛盾", "issues": []})
            TripReviewAgent(llm=Mock())._review_trip(req, p)
            payload = json.loads(model.return_value.run.call_args.args[0])
        context = payload["dining_preference_context"][0]
        self.assertEqual(context["meals"][0]["criteria"][1]["status"], "unknown")
        self.assertIn("contact_restaurant", context["available_choices"])
