"""固定方案、模拟地图与审核，验证步行上限，不访问外部服务。"""

import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from mcp.types import CallToolResult
from pydantic import ValidationError

from app.agents.review_agent import TripReviewAgent
from app.agents.trip_planner_agent import TripPlannerAgent, TripPlanningContext, TripPlanningError
from app.api.main import app
from app.models.schemas import Attraction, DayPlan, POIInfo, RoutePlan, RouteRequest, TripPlan, TripRequest
from app.services.amap_service import AmapService, AmapServiceError, AmapWalkingLimitError
from app.services.trip_workflow import TripPlanningWorkflow
from app.services.route_search import RouteSearchState


def request(**updates):
    values = dict(city="北京", start_date="2026-11-05", end_date="2026-11-05", travel_days=1,
                  transportation="公共交通", route_mode="transit", accommodation="酒店",
                  max_leg_walking_m=1000)
    values.update(updates)
    return TripRequest(**values)


def place(key):
    return POIInfo(id=key, name=f"景点{key}", address=f"地址{key}")


def route(origin="A", destination="B", walking=500, mode="transit", duration=600):
    return RoutePlan(origin=place(origin), destination=place(destination), mode=mode,
                     duration_s=duration, distance_m=walking if mode == "walking" else None,
                     walking_distance_m=walking if mode == "transit" else None)


def plan(keys=("A", "B")):
    return TripPlan(city="北京", start_date="2026-11-05", end_date="2026-11-05",
                    overall_suggestions="测试行程", days=[DayPlan(
                        date="2026-11-05", day_index=0, description="旧顺序",
                        transportation="公共交通", accommodation="酒店",
                        attractions=[Attraction(poi_id=key, name=f"景点{key}", address=f"地址{key}",
                                                description="游览建议", visit_duration=60) for key in keys],
                    )])


def transit_result(options):
    return CallToolResult(content=[], structuredContent={"transits": options})


class WalkingSchemaTests(unittest.TestCase):
    def test_optional_limit_is_backward_compatible_and_zero_is_valid(self):
        self.assertIsNone(request(max_leg_walking_m=None).max_leg_walking_m)
        self.assertEqual(request(max_leg_walking_m=0).max_leg_walking_m, 0)
        self.assertEqual(request(route_mode=None, transportation="地铁").max_leg_walking_m, 1000)

    def test_invalid_limits_and_unmeasurable_modes_are_rejected(self):
        for updates in ({"max_leg_walking_m": -1}, {"max_leg_walking_m": 50001},
                        {"max_leg_walking_m": 1.5}, {"route_mode": "driving"},
                        {"route_mode": None, "transportation": "飞机"}):
            with self.subTest(updates=updates), self.assertRaises(ValidationError):
                request(**updates)
        with self.assertRaises(ValidationError):
            RouteRequest(origin_poi_id="A", destination_poi_id="B", mode="driving", max_leg_walking_m=1000)

    def test_invalid_limit_stops_before_workflow(self):
        with TestClient(app) as client, patch("app.api.main.TripPlanningWorkflow") as workflow:
            response = client.post("/api/trip/plan", json={**request().model_dump(), "max_leg_walking_m": -1})
        self.assertEqual(response.status_code, 422)
        workflow.assert_not_called()

    def test_request_contexts_have_independent_route_state(self):
        first = TripPlanningContext([], [], None, [], None, [], None)
        second = TripPlanningContext([], [], None, [], None, [], None)
        first.route_search.begin_generation()
        self.assertIsNot(first.route_search, second.route_search)
        self.assertEqual(second.route_search.generation, 0)

    def test_model_revision_cannot_bypass_explicit_first_point_lock(self):
        prior = plan()
        changed = plan(("C", "D"))
        planner = TripPlannerAgent(llm=Mock(), amap_service=Mock())
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent:
            agent.return_value.run.return_value = json.dumps(changed.model_dump())
            with self.assertRaisesRegex(TripPlanningError, "首站"):
                planner._generate_plan(request(preserve_first_attraction=True),
                                       [place(key) for key in "ABCD"], [], previous_plan=prior)


class WalkingSelectionTests(unittest.TestCase):
    def test_fixed_provider_response_selects_eligible_option_instead_of_first(self):
        data = transit_result([
            {"duration": "300", "walking_distance": "2384"},
            {"duration": "900", "walking_distance": "900"},
            {"duration": "1200", "walking_distance": "500"},
        ])
        baseline = AmapService._parse_route(data, place("A"), place("B"), "transit")
        improved = AmapService._parse_route(data, place("A"), place("B"), "transit", 1000)
        self.assertEqual(baseline.walking_distance_m, 2384)
        self.assertEqual(improved.walking_distance_m, 900)
        self.assertEqual(improved.duration_s, 900)
        self.assertIn("1000", improved.selection_reason)

    def test_unknown_malformed_and_excessive_walking_do_not_become_zero(self):
        for option in ({"duration": "300"}, {"duration": "300", "walking_distance": "unknown"},
                       {"duration": "300", "walking_distance": "1001"}):
            with self.subTest(option=option), self.assertRaises(AmapWalkingLimitError):
                AmapService._parse_route(transit_result([option]), place("A"), place("B"), "transit", 1000)

    def test_zero_and_exact_boundary_are_accepted(self):
        for limit in (0, 1000):
            selected = AmapService._parse_route(transit_result([
                {"duration": "300", "walking_distance": str(limit)},
            ]), place("A"), place("B"), "transit", limit)
            self.assertEqual(selected.walking_distance_m, limit)

    def test_malformed_first_option_does_not_hide_a_later_eligible_one(self):
        selected = AmapService._parse_route(transit_result([
            {"duration": "unknown", "walking_distance": "10"},
            {"duration": "300", "walking_distance": "800"},
        ]), place("A"), place("B"), "transit", 1000)
        self.assertEqual(selected.walking_distance_m, 800)

    def test_failure_distinguishes_known_excess_from_unknown_options(self):
        known = [{"duration": "300", "walking_distance": "2384"},
                 {"duration": "600", "walking_distance": "1778"}]
        for options, status in [(known, "over_limit"),
                                (known + [{"duration": "300"}], "walking_unknown")]:
            with self.subTest(status=status), self.assertRaises(AmapWalkingLimitError) as caught:
                AmapService._parse_route(transit_result(options), place("A"), place("B"), "transit", 1000)
            self.assertEqual(caught.exception.code, status)
            self.assertEqual(caught.exception.minimum_walking_m, 1778)


class WalkingPlanningTests(unittest.IsolatedAsyncioTestCase):
    def make_planner(self):
        maps = Mock()
        maps.plan_route = AsyncMock()
        return TripPlannerAgent(llm=Mock(), amap_service=maps)

    async def test_planner_independently_rejects_an_oversized_or_unknown_route(self):
        planner = self.make_planner()
        for distance in (2384, None):
            planner.amap_service.plan_route.return_value = route(walking=distance)
            with self.assertRaises(AmapWalkingLimitError):
                await planner._query_route("A", "B", "transit", "北京", 1000)

    async def test_auto_limit_overrides_short_walk_preference(self):
        planner = self.make_planner()
        planner.amap_service.plan_route.side_effect = [route(walking=1200, mode="walking"), route(walking=500)]
        selected = await planner._recommend_route("A", "B", "北京", 1000)
        self.assertEqual(selected.mode, "transit")
        self.assertTrue(all(call.args[0].max_leg_walking_m == 1000 for call in planner.amap_service.plan_route.await_args_list))

    async def test_replacement_preserves_first_point_and_count_and_refreshes_description(self):
        planner = self.make_planner()
        async def lookup(query):
            if query.destination_poi_id == "B":
                raise AmapWalkingLimitError("No qualifying scheme")
            return route(query.origin_poi_id, query.destination_poi_id, 500)
        planner.amap_service.plan_route.side_effect = lookup
        current = plan()
        await planner._attach_routes(current, request(), [place(key) for key in ("A", "B", "C")])
        day = current.days[0]
        self.assertEqual([item.poi_id for item in day.attractions], ["A", "C"])
        self.assertEqual([(item.origin.id, item.destination.id) for item in day.routes], [("A", "C")])
        self.assertIn("景点C", day.description)
        self.assertNotIn("景点B", day.description)
        self.assertEqual(planner.amap_service.plan_route.await_count, 2)

    async def test_replacement_is_committed_only_after_outgoing_route_is_also_valid(self):
        planner = self.make_planner()
        valid = {("A", "C"), ("A", "E"), ("E", "D")}
        async def lookup(query):
            pair = (query.origin_poi_id, query.destination_poi_id)
            if pair not in valid:
                raise AmapWalkingLimitError("No qualifying scheme")
            return route(*pair, walking=500)
        planner.amap_service.plan_route.side_effect = lookup
        current = plan(("A", "B", "D"))
        await planner._attach_routes(current, request(), [place(key) for key in ("A", "B", "D", "C", "E")])
        self.assertEqual([item.poi_id for item in current.days[0].attractions], ["A", "E", "D"])
        self.assertEqual([(item.origin.id, item.destination.id) for item in current.days[0].routes], [("A", "E"), ("E", "D")])
        # V4.1在替换之前也先查询原B→D，确保其他路段获得初查机会。
        self.assertEqual(planner.amap_service.plan_route.await_count, 6)

    async def test_unrepairable_route_has_bounded_attempts_and_keeps_original_attractions(self):
        planner = self.make_planner()
        planner.amap_service.plan_route.side_effect = AmapWalkingLimitError("No qualifying scheme")
        current = plan()
        await planner._attach_routes(current, request(), [place(key) for key in ("A", "B", "C", "D", "E", "F", "G", "H")])
        self.assertEqual([item.poi_id for item in current.days[0].attractions], ["A", "B"])
        self.assertEqual(current.days[0].routes, [])
        self.assertLessEqual(planner.amap_service.plan_route.await_count, 13)
        self.assertTrue(any("不能视为达标" in text for text in current.days[0].route_warnings))
        self.assertIn("尚未全部满足或验证", current.overall_suggestions)

    async def test_whole_pair_replacement_finds_the_counterexample_that_v1_missed(self):
        planner = self.make_planner()
        async def lookup(query):
            pair = (query.origin_poi_id, query.destination_poi_id)
            if pair != ("C", "D"):
                raise AmapWalkingLimitError("No eligible edge", code="over_limit", minimum_walking_m=2000)
            return route(*pair, walking=500)
        planner.amap_service.plan_route.side_effect = lookup
        current = plan()
        await planner._attach_routes(current, request(), [place(key) for key in ("A", "B", "C", "D")])
        self.assertEqual([item.poi_id for item in current.days[0].attractions], ["C", "D"])
        self.assertEqual(current.days[0].routes[0].walking_distance_m, 500)
        self.assertEqual(len(current.days[0].attractions), 2)

    async def test_explicit_first_point_lock_does_not_silently_change_anchor(self):
        planner = self.make_planner()
        async def lookup(query):
            if (query.origin_poi_id, query.destination_poi_id) == ("C", "D"):
                return route("C", "D")
            raise AmapWalkingLimitError("No eligible edge")
        planner.amap_service.plan_route.side_effect = lookup
        current = plan()
        await planner._attach_routes(current, request(preserve_first_attraction=True), [place(key) for key in ("A", "B", "C", "D")])
        self.assertEqual([item.poi_id for item in current.days[0].attractions], ["A", "B"])
        self.assertEqual(current.days[0].routes, [])

    async def test_success_and_deterministic_failures_are_reused_across_generations(self):
        planner = self.make_planner()
        async def lookup(query):
            if (query.origin_poi_id, query.destination_poi_id) == ("C", "D"):
                return route("C", "D")
            raise AmapWalkingLimitError("No eligible edge", code="over_limit")
        planner.amap_service.plan_route.side_effect = lookup
        state = RouteSearchState()
        candidates = [place(key) for key in ("A", "B", "C", "D")]
        first = plan()
        await planner._attach_routes(first, request(), candidates, search_state=state)
        calls = planner.amap_service.plan_route.await_count
        second = plan()
        await planner._attach_routes(second, request(), candidates, search_state=state)
        self.assertEqual(planner.amap_service.plan_route.await_count, calls)
        self.assertEqual([item.poi_id for item in second.days[0].attractions], ["C", "D"])
        self.assertTrue(any(item.cache_hit and item.generation == 2 for item in second.route_diagnostics))
        self.assertTrue(all(item.generation == 1 for item in first.route_diagnostics))

    async def test_transient_failure_can_retry_in_next_generation_but_not_forever(self):
        planner = self.make_planner()
        planner.amap_service.plan_route.side_effect = [TimeoutError(), route()]
        state = RouteSearchState()
        for _ in range(3):
            current = plan()
            await planner._attach_routes(current, request(), [], search_state=state)
        self.assertEqual(planner.amap_service.plan_route.await_count, 2)
        self.assertEqual([item.status for item in state.diagnostics if not item.cache_hit], ["timeout", "available"])
        self.assertEqual(len(current.days[0].routes), 1)

    async def test_trace_separates_walking_unknown_from_over_limit_and_sanitizes_errors(self):
        for error, status in [(AmapWalkingLimitError("private", code="over_limit", minimum_walking_m=2384), "over_limit"),
                              (AmapWalkingLimitError("private"), "walking_unknown"),
                              (AmapServiceError("private"), "provider_error")]:
            planner = self.make_planner()
            planner.amap_service.plan_route.side_effect = error
            current = plan()
            await planner._attach_routes(current, request(), [])
            self.assertEqual(current.route_diagnostics[0].status, status)
            self.assertNotIn("private", current.model_dump_json())

    async def test_repeated_transient_failure_stops_after_two_actual_attempts(self):
        planner = self.make_planner()
        planner.amap_service.plan_route.side_effect = TimeoutError()
        state = RouteSearchState()
        for _ in range(3):
            current = plan()
            await planner._attach_routes(current, request(), [], search_state=state)
        self.assertEqual(planner.amap_service.plan_route.await_count, 2)
        self.assertEqual(current.days[0].routes, [])
        self.assertTrue(state.diagnostics[-1].cache_hit)

    async def test_search_can_replace_a_whole_three_stop_path(self):
        planner = self.make_planner()
        async def lookup(query):
            pair = (query.origin_poi_id, query.destination_poi_id)
            if pair in {("D", "E"), ("E", "F")}:
                return route(*pair)
            raise AmapWalkingLimitError("No eligible edge", code="over_limit")
        planner.amap_service.plan_route.side_effect = lookup
        current = plan(("A", "B", "C"))
        await planner._attach_routes(current, request(), [place(key) for key in "ABCDEF"])
        self.assertEqual([item.poi_id for item in current.days[0].attractions], ["D", "E", "F"])
        self.assertEqual(len(current.days[0].routes), 2)
        self.assertTrue(all(item.walking_distance_m <= 1000 for item in current.days[0].routes))

    async def test_repair_does_not_introduce_a_place_already_used_on_another_day(self):
        planner = self.make_planner()
        async def lookup(query):
            pair = (query.origin_poi_id, query.destination_poi_id)
            if pair in {("C", "D"), ("E", "F")}:
                return route(*pair)
            raise AmapWalkingLimitError("No eligible edge", code="over_limit")
        planner.amap_service.plan_route.side_effect = lookup
        current = plan()
        second_day = plan(("C", "D")).days[0]
        second_day.date, second_day.day_index = "2026-11-06", 1
        current.days.append(second_day)
        current.end_date = "2026-11-06"
        await planner._attach_routes(current, request(end_date="2026-11-06", travel_days=2),
                                    [place(key) for key in "ABCDEF"])
        self.assertEqual([item.poi_id for item in current.days[0].attractions], ["E", "F"])
        self.assertEqual([item.poi_id for item in current.days[1].attractions], ["C", "D"])

    async def test_independent_requests_do_not_share_cached_failures(self):
        planner = self.make_planner()
        planner.amap_service.plan_route.side_effect = [AmapWalkingLimitError("No eligible edge"), route()]
        first, second = plan(), plan()
        await planner._attach_routes(first, request(), [], search_state=RouteSearchState())
        await planner._attach_routes(second, request(), [], search_state=RouteSearchState())
        self.assertEqual(planner.amap_service.plan_route.await_count, 2)
        self.assertEqual(first.days[0].routes, [])
        self.assertEqual(len(second.days[0].routes), 1)

    async def test_service_receives_numeric_limit(self):
        service = AmapService()
        data = transit_result([{"duration": "100", "walking_distance": "1500"}, {"duration": "300", "walking_distance": "900"}])
        with patch.object(service, "get_poi_detail", new_callable=AsyncMock, side_effect=[
            POIInfo(id="A", name="A", location={"longitude": 116.4, "latitude": 39.9}),
            POIInfo(id="B", name="B", location={"longitude": 116.5, "latitude": 39.9}),
        ]), patch.object(service, "_call_tool", new_callable=AsyncMock, return_value=data):
            selected = await service.plan_route(RouteRequest(origin_poi_id="A", destination_poi_id="B", mode="transit", city="北京", max_leg_walking_m=1000))
        self.assertEqual(selected.walking_distance_m, 900)


class WalkingReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_deterministic_failure_cannot_be_overruled_by_a_model_pass(self):
        reviewer = TripReviewAgent(llm=Mock())
        for routes in ([], [route(walking=2384)], [route(walking=None)]):
            current = plan()
            current.days[0].routes = routes
            with patch("app.agents.review_agent.SimpleAgent") as agent:
                result = await reviewer.review_trip(request(), current)
                agent.assert_not_called()
            self.assertFalse(result.passed)
            self.assertIn("1000", result.issues[0].evidence)

    async def test_workflow_waits_for_user_choice_instead_of_rewriting_missing_routes(self):
        planner = Mock()
        planner.llm = Mock()
        planner.prepare_context = AsyncMock(return_value=object())
        planner.generate_plan = AsyncMock(side_effect=[plan(), plan(), plan()])
        reviewer = TripReviewAgent(llm=Mock())
        with patch("app.agents.review_agent.SimpleAgent") as model:
            result = await TripPlanningWorkflow(planner, reviewer).plan_trip(request(enable_review=True))
        model.assert_not_called()
        self.assertEqual(result.review.status, "needs_revision")
        self.assertEqual(result.review.revisions, 0)
        self.assertEqual(result.review.review_count, 0)
        planner.generate_plan.assert_awaited_once()
        self.assertEqual(result.route_resolution.status, "needs_choice")


if __name__ == "__main__":
    unittest.main()
