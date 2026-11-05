"""无数值步行上限时，也须修复路线且保持用户约束；不调用真实 API。"""

import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.agents.review_agent import TripReviewAgent
from app.agents.trip_planner_agent import TripPlannerAgent, TripPlanningContext
from app.services.amap_service import AmapRouteNotFoundError, AmapServiceError
from app.services.route_policy import route_problem
from app.services.route_search import RouteSearchState
from test_walking_limits import plan, place, request, route
import test_dining_service as dining_tests
from app.services.dining_service import DiningService, DiningState


class RouteRepairTests(unittest.IsolatedAsyncioTestCase):
    def planner(self, valid):
        maps = Mock()
        async def lookup(query):
            pair = query.origin_poi_id, query.destination_poi_id
            if pair not in valid:
                raise AmapRouteNotFoundError("private provider response")
            return route(*pair, walking=1256, mode=query.mode)
        maps.plan_route = AsyncMock(side_effect=lookup)
        return TripPlannerAgent(llm=Mock(), amap_service=maps)

    async def test_no_cap_repairs_local_pair_without_inventing_a_walking_limit(self):
        planner = self.planner({("A", "C")})
        current = plan()
        await planner._attach_routes(current, request(max_leg_walking_m=None), [place(k) for k in "ABC"])
        self.assertEqual([a.poi_id for a in current.days[0].attractions], ["A", "C"])
        self.assertEqual(len(current.days[0].routes), 1)
        self.assertEqual(current.days[0].routes[0].walking_distance_m, 1256)
        self.assertEqual(current.days[0].routes[0].mode, "transit")
        self.assertNotIn("景点B", current.days[0].description)
        self.assertTrue(all(c.args[0].max_leg_walking_m is None for c in planner.amap_service.plan_route.call_args_list))

    async def test_no_cap_whole_pair_and_cache_reused_across_generations(self):
        planner = self.planner({("C", "D")})
        state = RouteSearchState()
        candidates = [place(k) for k in "ABCD"]
        for generation in range(2):
            current = plan()
            await planner._attach_routes(current, request(max_leg_walking_m=None), candidates, state)
            self.assertEqual([a.poi_id for a in current.days[0].attractions], ["C", "D"])
            if generation == 0:
                calls = planner.amap_service.plan_route.await_count
        self.assertEqual(planner.amap_service.plan_route.await_count, calls)
        self.assertEqual(state.generation, 2)
        self.assertTrue(any(d.cache_hit and d.generation == 2 for d in current.route_diagnostics))

    async def test_no_cap_keeps_first_lock_and_bounded_failure_cannot_pass_review(self):
        planner = self.planner({("C", "D")})
        req = request(max_leg_walking_m=None, preserve_first_attraction=True)
        current = plan()
        await planner._attach_routes(current, req, [place(k) for k in "ABCDEFGH"])
        self.assertEqual([a.poi_id for a in current.days[0].attractions], ["A", "B"])
        self.assertEqual(current.days[0].routes, [])
        self.assertLessEqual(planner.amap_service.plan_route.await_count, 13)
        self.assertNotIn("private provider response", current.model_dump_json())
        with patch("app.agents.review_agent.SimpleAgent") as model:
            result = await TripReviewAgent(llm=Mock()).review_trip(req, current)
        self.assertFalse(result.passed)
        self.assertIn("路线缺失", result.issues[0].evidence)
        model.assert_not_called()

    async def test_no_cap_checks_both_affected_edges_before_replacement(self):
        planner = self.planner({("A", "C"), ("A", "E"), ("E", "D")})
        current = plan(("A", "B", "D"))
        await planner._attach_routes(current, request(max_leg_walking_m=None), [place(k) for k in "ABDCE"])
        self.assertEqual([a.poi_id for a in current.days[0].attractions], ["A", "E", "D"])
        self.assertEqual([(r.origin.id, r.destination.id) for r in current.days[0].routes], [("A", "E"), ("E", "D")])

    async def test_healthy_plan_does_not_probe_replacements(self):
        planner = self.planner({("A", "B")})
        current = plan()
        await planner._attach_routes(current, request(max_leg_walking_m=None), [place(k) for k in "ABCD"])
        self.assertEqual([a.poi_id for a in current.days[0].attractions], ["A", "B"])
        self.assertEqual(planner.amap_service.plan_route.await_count, 1)

    async def test_no_cap_revision_receives_previous_route_failures(self):
        planner = self.planner(set())
        req = request(max_leg_walking_m=None)
        context = TripPlanningContext([place(k) for k in "AB"], [], None, [], None, [], None)
        await planner._attach_routes(plan(), req, [], context.route_search)
        with patch.object(planner, "_generate_plan", return_value=plan()) as generate, patch.object(planner, "_attach_routes", new_callable=AsyncMock):
            await planner.generate_plan(req, context)
        evidence = generate.call_args.kwargs["route_evidence"]
        self.assertEqual(evidence[0]["status"], "no_route")
        self.assertIsNone(evidence[0]["max_leg_walking_m"])

    async def test_no_cap_transient_retry_is_bounded_and_cache_respects_mode(self):
        planner = self.planner(set())
        planner.amap_service.plan_route.side_effect = AmapServiceError("private")
        state = RouteSearchState()
        for _ in range(3):
            await planner._attach_routes(plan(), request(max_leg_walking_m=None), [], state)
        self.assertEqual(planner.amap_service.plan_route.await_count, 2)
        planner.amap_service.plan_route.side_effect = lambda q: route(mode=q.mode)
        await planner._attach_routes(plan(), request(max_leg_walking_m=None, route_mode="walking"), [], state)
        self.assertEqual(planner.amap_service.plan_route.await_count, 3)

    async def test_review_rejects_wrong_mode_without_numeric_limit(self):
        current = plan()
        current.days[0].routes = [route(mode="walking")]
        with patch("app.agents.review_agent.SimpleAgent") as model:
            result = await TripReviewAgent(llm=Mock()).review_trip(request(max_leg_walking_m=None), current)
        self.assertFalse(result.passed)
        model.assert_not_called()

    async def test_dining_queries_pending_no_cap_routes_and_reuses_unchanged_anchors(self):
        maps = dining_tests.DiningTests().maps()
        req, current = dining_tests.inputs()
        saved = current.days[0].routes
        current.days[0].routes = []
        state = DiningState()
        await DiningService(maps).finalize(current, req, state)
        self.assertEqual(maps._call_tool.await_count, 2)
        self.assertEqual((state.searches, state.details), (2, 0))
        self.assertEqual(len(current.days[0].meals), 2)
        self.assertTrue(current.days[0].dining_resolution.based_on_provisional_itinerary)
        current.days[0].routes = saved
        await DiningService(maps).finalize(current, req, state)
        self.assertEqual(len(current.days[0].meals), 2)
        self.assertEqual(maps._call_tool.await_count, 2)

    def test_shared_policy_preserves_zero_cap_and_auto_modes(self):
        self.assertIsNone(route_problem(route(walking=0), "transit", 0))
        self.assertIsNotNone(route_problem(route(walking=1), "transit", 0))
        self.assertIsNone(route_problem(route(walking=None), "transit"))
        self.assertIsNone(route_problem(route(mode="walking"), "auto"))
        self.assertIsNotNone(route_problem(route(mode="driving"), "auto"))
