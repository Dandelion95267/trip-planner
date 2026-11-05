"""缺少可行路线时如实交付、保留约束并等待用户选择；不调用外部服务。"""

import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.main import app
from app.agents.trip_planner_agent import TripPlannerAgent
from app.models.schemas import RouteDiagnostic, RouteResolution
from app.services.route_resolution import build_route_resolution
from app.services.trip_workflow import TripPlanningWorkflow
from test_walking_limits import plan, request, route, place
import test_trip_workflow as workflow_tests


def diagnostic(status="no_route", **updates):
    data = dict(generation=1, origin_poi_id="A", destination_poi_id="B", mode="transit",
                max_leg_walking_m=1000, status=status, attempt=1, message="not trusted for display")
    data.update(updates)
    return RouteDiagnostic(**data)


class ResolutionTests(unittest.TestCase):
    def test_partial_result_preserves_verified_routes_and_all_inputs(self):
        req = request(preserve_first_attraction=True)
        current = plan(("A", "B", "C"))
        current.days[0].routes = [route("B", "C", walking=708)]
        current.route_diagnostics = [diagnostic()]
        before = current.model_dump(), req.model_dump()
        result = build_route_resolution(req, current)
        self.assertEqual((result.status, result.required_segments, result.verified_segments), ("needs_choice", 2, 1))
        self.assertEqual(result.unresolved[0].reason, "no_route")
        self.assertEqual((current.model_dump(), req.model_dump()), before)
        self.assertTrue(result.preserve_first_attraction)
        self.assertTrue(all(x.requires_confirmation and x.verification == "not_verified" for x in result.alternatives))
        self.assertIn("不证明", result.unresolved[0].explanation)
        self.assertNotIn("not trusted", result.model_dump_json())

    def test_observed_over_limit_distance_is_only_a_conditional_suggestion(self):
        req, current = request(), plan()
        current.route_diagnostics = [diagnostic("over_limit", minimum_returned_walking_m=1256)]
        result = build_route_resolution(req, current)
        option = next(x for x in result.alternatives if x.kind == "relax_walking_limit")
        self.assertEqual(option.suggested_max_leg_walking_m, 1256)
        self.assertIn("不保证整趟行程可行", option.description)
        self.assertEqual(req.max_leg_walking_m, 1000)
        self.assertEqual(current.days[0].routes, [])

    def test_provider_and_budget_failures_are_not_reported_as_proven_no_route(self):
        for status in ("provider_error", "timeout", "budget_exhausted", "walking_unknown", "invalid_response"):
            with self.subTest(status=status):
                current = plan()
                current.route_diagnostics = [diagnostic(status)]
                result = build_route_resolution(request(), current)
                self.assertEqual(result.unresolved[0].reason, status)
                self.assertIsNone(result.unresolved[0].observed_walking_m)

    def test_stale_mode_or_limit_diagnostics_cannot_justify_suggestions(self):
        for updates in ({"mode": "walking"}, {"max_leg_walking_m": 2000}, {"origin_poi_id": "X"}):
            current = plan()
            current.route_diagnostics = [diagnostic("over_limit", minimum_returned_walking_m=3000, **updates)]
            result = build_route_resolution(request(), current)
            self.assertEqual(result.unresolved[0].reason, "not_queried")
            self.assertTrue(all(x.suggested_max_leg_walking_m is None for x in result.alternatives))

    def test_latest_diagnostic_is_used(self):
        current = plan()
        current.route_diagnostics = [diagnostic(), diagnostic("timeout", generation=2)]
        self.assertEqual(build_route_resolution(request(), current).unresolved[0].reason, "timeout")

    def test_zero_cap_is_kept_and_fully_verified_routes_have_no_alternatives(self):
        req, current = request(max_leg_walking_m=0), plan()
        current.days[0].routes = [route(walking=0)]
        result = build_route_resolution(req, current)
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.max_leg_walking_m, 0)
        self.assertEqual(result.alternatives, [])

    def test_no_pairs_are_not_claimed_as_a_verified_trip(self):
        result = build_route_resolution(request(), plan(("A",)))
        self.assertEqual(result.status, "not_applicable")
        self.assertEqual(result.verified_segments, 0)

    def test_wrong_mode_and_missing_ids_do_not_count_as_verified(self):
        current = plan()
        current.days[0].routes = [route(mode="driving")]
        self.assertEqual(build_route_resolution(request(), current).unresolved[0].reason, "mode_mismatch")
        current.days[0].attractions[0].poi_id = None
        self.assertEqual(build_route_resolution(request(), current).unresolved[0].reason, "missing_poi_id")

    def test_unknown_mode_and_maximum_cap_have_safe_options(self):
        result = build_route_resolution(request(max_leg_walking_m=None, route_mode=None, transportation="飞机"), plan())
        self.assertEqual(result.unresolved[0].reason, "unsupported_mode")
        result = build_route_resolution(request(max_leg_walking_m=50000), plan())
        self.assertNotIn("relax_walking_limit", [x.kind for x in result.alternatives])

    def test_schema_rejects_false_coverage_and_pre_applied_options(self):
        result = build_route_resolution(request(), plan()).model_dump()
        bad = {**result, "verified_segments": 1}
        with self.assertRaises(ValidationError):
            RouteResolution.model_validate(bad)
        result["alternatives"][0]["requires_confirmation"] = False
        with self.assertRaises(ValidationError):
            RouteResolution.model_validate(result)

    def test_llm_cannot_forge_resolution_or_see_its_schema(self):
        current = plan()
        payload = current.model_dump()
        payload["route_resolution"] = {"status": "verified", "malformed": "ignore this"}
        planner = TripPlannerAgent(llm=Mock(), amap_service=Mock())
        with patch("app.agents.trip_planner_agent.SimpleAgent") as model:
            model.return_value.run.return_value = json.dumps(payload)
            generated = planner._generate_plan(request(), [place("A"), place("B")], [])
        self.assertIsNone(generated.route_resolution)
        self.assertNotIn("RouteResolution", json.dumps(planner._model_schema()))


class ResolutionWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def workflow(self, outputs):
        planner = Mock()
        planner.prepare_context = AsyncMock(return_value=object())
        planner.generate_plan = AsyncMock(side_effect=outputs)
        planner.plan_trip = AsyncMock(return_value=outputs[0])
        reviewer = Mock(review_trip=AsyncMock())
        return TripPlanningWorkflow(planner, reviewer), planner, reviewer

    async def test_unresolved_routes_stop_after_one_generation_even_if_reviewer_could_pass(self):
        req, current = request(enable_review=True), plan()
        current.route_diagnostics = [diagnostic()]
        workflow, planner, reviewer = self.workflow([current])
        before = req.model_dump()
        result = await workflow.plan_trip(req)
        self.assertIs(result, current)
        self.assertEqual(result.route_resolution.status, "needs_choice")
        self.assertEqual(result.review.status, "needs_revision")
        self.assertEqual((result.review.review_count, result.review.revisions), (0, 0))
        planner.generate_plan.assert_awaited_once()
        reviewer.review_trip.assert_not_awaited()
        self.assertEqual(req.model_dump(), before)

    async def test_disabled_model_review_still_explains_missing_routes(self):
        workflow, _, reviewer = self.workflow([plan()])
        result = await workflow.plan_trip(request(enable_review=False))
        self.assertEqual(result.route_resolution.status, "needs_choice")
        self.assertIsNone(result.review)
        reviewer.review_trip.assert_not_awaited()

    async def test_revision_that_loses_route_coverage_waits_without_further_rewrite(self):
        req, good, failed, _ = workflow_tests.make_inputs()
        unresolved = good.model_copy(deep=True)
        unresolved.days[0].routes = []
        workflow, planner, reviewer = self.workflow([good, unresolved])
        reviewer.review_trip.return_value = failed
        result = await workflow.plan_trip(req)
        self.assertIs(result, unresolved)
        self.assertEqual(result.route_resolution.status, "needs_choice")
        self.assertEqual((result.review.review_count, result.review.revisions), (1, 1))
        self.assertEqual(planner.generate_plan.await_count, 2)
        self.assertEqual(reviewer.review_trip.await_count, 1)


class ResolutionEndpointTests(unittest.TestCase):
    def test_api_returns_partial_plan_and_choices_without_applying_any_change(self):
        current = plan(("A", "B", "C"))
        current.days[0].routes = [route("B", "C", walking=708)]
        planner = Mock(prepare_context=AsyncMock(return_value=object()), generate_plan=AsyncMock(return_value=current))
        with TestClient(app) as client, patch("app.api.main.TripPlannerAgent", return_value=planner), patch("app.services.trip_workflow.TripReviewAgent") as reviewer:
            response = client.post("/api/trip/plan", json=request(enable_review=True).model_dump())
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["route_resolution"]["max_leg_walking_m"], 1000)
        self.assertEqual(result["route_resolution"]["verified_segments"], 1)
        self.assertEqual(result["review"]["status"], "needs_revision")
        self.assertEqual([x["poi_id"] for x in result["days"][0]["attractions"]], ["A", "B", "C"])
        self.assertEqual(result["days"][0]["routes"][0]["walking_distance_m"], 708)
        reviewer.assert_not_called()
