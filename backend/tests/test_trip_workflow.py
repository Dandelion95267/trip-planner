"""验证审核修改循环、次数限制、失败保留和实际规划器的数据校验。"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

from app.agents.review_agent import TripReviewError
from app.agents.trip_planner_agent import TripPlannerAgent, TripPlanningError
from app.api.main import app
from app.models.schemas import POIInfo, RoutePlan, TripPlan, TripRequest, TripReviewResult, TripReviewSummary
from app.services.amap_service import AmapServiceError
from app.services.trip_workflow import TripPlanningWorkflow


def make_inputs():
    request = TripRequest(
        city="北京", start_date="2026-11-01", end_date="2026-11-01", travel_days=1,
        transportation="公共交通", accommodation="经济型酒店", enable_review=True,
    )
    plan = TripPlan(
        city=request.city, start_date=request.start_date, end_date=request.end_date,
        days=[{
            "date": request.start_date, "day_index": 0, "description": "依次游览景点。",
            "transportation": request.transportation, "accommodation": request.accommodation,
            "attractions": [
                {"poi_id": key, "name": f"景点{key}", "address": "真实地址",
                 "visit_duration": 120, "description": "参观建议。"}
                for key in ("a", "b", "c")
            ],
        }], overall_suggestions="出行前确认预约条件。",
    )
    failed = TripReviewResult(passed=False, summary="行程偏满。", issues=[{
        "category": "pace", "day_index": 0, "problem": "一天游览时间偏长。",
        "evidence": "安排了三个各120分钟的景点。", "suggestion": "减少一个景点。",
    }])
    passed = TripReviewResult(passed=True, summary="未发现待修改问题。")
    return request, plan, failed, passed


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.request, self.plan, self.failed, self.passed = make_inputs()
        self.context = object()
        self.planner = Mock()
        self.planner.prepare_context = AsyncMock(return_value=self.context)
        self.planner.generate_plan = AsyncMock(return_value=self.plan)
        self.planner.plan_trip = AsyncMock(return_value=self.plan)
        self.reviewer = Mock()
        self.reviewer.review_trip = AsyncMock(return_value=self.passed)
        self.workflow = TripPlanningWorkflow(self.planner, self.reviewer)

    async def test_disabled_review_preserves_original_path(self):
        self.request.enable_review = False
        result = await self.workflow.plan_trip(self.request)
        self.assertIs(result, self.plan)
        self.assertIsNone(result.review)
        self.planner.prepare_context.assert_not_awaited()
        self.reviewer.review_trip.assert_not_awaited()

    async def test_pass_on_first_review_stops_without_revision(self):
        result = await self.workflow.plan_trip(self.request)
        self.assertEqual(result.review.status, "passed")
        self.assertEqual(result.review.revisions, 0)
        self.assertEqual(result.review.review_count, 1)
        self.planner.generate_plan.assert_awaited_once_with(self.request, self.context)

    async def test_revision_uses_feedback_and_same_context_then_reviews_new_plan(self):
        revised = self.plan.model_copy(deep=True)
        revised.days[0].attractions.pop()
        self.planner.generate_plan.side_effect = [self.plan, revised]
        self.reviewer.review_trip.side_effect = [self.failed, self.passed]
        result = await self.workflow.plan_trip(self.request)
        self.assertIs(result, revised)
        self.assertEqual(result.review.revisions, 1)
        self.assertEqual(result.review.review_count, 2)
        self.planner.prepare_context.assert_awaited_once_with(self.request)
        call = self.planner.generate_plan.await_args_list[1]
        self.assertIs(call.args[1], self.context)
        self.assertEqual(call.kwargs["review_feedback"], self.failed)
        self.assertIsNot(call.kwargs["previous_plan"], self.plan)
        self.assertIs(self.reviewer.review_trip.await_args_list[1].args[1], revised)
        self.assertIsNone(self.plan.review)

    async def test_two_revision_limit_preserves_remaining_issues(self):
        plans = [self.plan.model_copy(deep=True) for _ in range(3)]
        self.planner.generate_plan.side_effect = plans
        self.reviewer.review_trip.return_value = self.failed
        result = await self.workflow.plan_trip(self.request)
        self.assertIs(result, plans[-1])
        self.assertEqual(result.review.status, "needs_revision")
        self.assertEqual(result.review.revisions, 2)
        self.assertEqual(result.review.review_count, 3)
        self.assertFalse(result.review.result.passed)
        self.assertEqual(self.planner.generate_plan.await_count, 3)
        self.assertEqual(self.reviewer.review_trip.await_count, 3)

    async def test_failed_review_returns_valid_plan_without_claiming_pass(self):
        self.reviewer.review_trip.side_effect = TripReviewError("private upstream error")
        result = await self.workflow.plan_trip(self.request)
        self.assertIs(result, self.plan)
        self.assertEqual(result.review.status, "unavailable")
        self.assertIsNone(result.review.result)
        self.assertNotIn("private", result.model_dump_json())

    async def test_review_failure_after_revision_does_not_attach_old_verdict(self):
        revised = self.plan.model_copy(deep=True)
        self.planner.generate_plan.side_effect = [self.plan, revised]
        self.reviewer.review_trip.side_effect = [self.failed, TripReviewError("failed")]
        result = await self.workflow.plan_trip(self.request)
        self.assertIs(result, revised)
        self.assertEqual(result.review.status, "unavailable")
        self.assertEqual(result.review.revisions, 1)
        self.assertIsNone(result.review.result)

    async def test_invalid_revision_keeps_previous_valid_plan_and_its_issues(self):
        self.planner.generate_plan.side_effect = [self.plan, TripPlanningError("private parse error")]
        self.reviewer.review_trip.return_value = self.failed
        result = await self.workflow.plan_trip(self.request)
        self.assertIs(result, self.plan)
        self.assertEqual(result.review.status, "needs_revision")
        self.assertEqual(result.review.revisions, 0)
        self.assertEqual(result.review.result, self.failed)
        self.assertNotIn("private", result.model_dump_json())
        self.assertEqual(self.reviewer.review_trip.await_count, 1)

    async def test_timeout_returns_valid_plan(self):
        with patch("app.services.trip_workflow.REVIEW_BUDGET_SECONDS", 0):
            result = await self.workflow.plan_trip(self.request)
        self.assertEqual(result.review.status, "unavailable")

    async def test_revision_timeout_keeps_previous_valid_plan(self):
        async def generate(*args, **kwargs):
            if kwargs:
                await asyncio.sleep(1)
            return self.plan

        self.planner.generate_plan.side_effect = generate
        self.reviewer.review_trip.return_value = self.failed
        with patch("app.services.trip_workflow.REVIEW_BUDGET_SECONDS", 0.02):
            result = await self.workflow.plan_trip(self.request)
        self.assertIs(result, self.plan)
        self.assertEqual(result.review.status, "needs_revision")
        self.assertEqual(result.review.revisions, 0)

    async def test_initial_generation_failure_is_not_hidden(self):
        self.planner.generate_plan.side_effect = TripPlanningError("initial failure")
        with self.assertRaises(TripPlanningError):
            await self.workflow.plan_trip(self.request)
        self.reviewer.review_trip.assert_not_awaited()


class PlannerRevisionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_revision_revalidates_pois_and_queries_routes_for_new_order(self):
        request, source, failed, passed = make_inputs()
        places = {key: POIInfo(id=key, name=f"真实景点{key}", address="真实地址") for key in ("a", "b", "c")}
        maps = Mock()
        maps.search_poi = AsyncMock(return_value=list(places.values()))
        maps.get_weather = AsyncMock(return_value=[])
        maps.search_hotels = AsyncMock(return_value=[])
        maps.search_restaurants = AsyncMock(return_value=[])

        async def route(query):
            return RoutePlan(origin=places[query.origin_poi_id], destination=places[query.destination_poi_id],
                             mode=query.mode, duration_s=600)

        maps.plan_route = AsyncMock(side_effect=route)
        planner = TripPlannerAgent(llm=Mock(), amap_service=maps)
        reviewer = Mock()
        reviews = iter([failed, passed])
        seen_review_summaries = []

        async def review(current_request, current_plan):
            seen_review_summaries.append(current_plan.review)
            return next(reviews)

        reviewer.review_trip = AsyncMock(side_effect=review)
        revised = source.model_copy(deep=True)
        revised.days[0].attractions.pop(1)
        revised.days[0].routes = [RoutePlan(origin=places["b"], destination=places["c"], mode="transit", duration_s=1)]
        revised.review = TripReviewSummary(status="passed", review_count=1, revisions=0,
                                          result=passed, message="模型声称已通过")
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent:
            agent.return_value.run.side_effect = [source.model_dump_json(), revised.model_dump_json()]
            result = await TripPlanningWorkflow(planner, reviewer).plan_trip(request)
        self.assertEqual([item.name for item in result.days[0].attractions], ["真实景点a", "真实景点c"])
        self.assertEqual([(route.origin.id, route.destination.id) for route in result.days[0].routes], [("a", "c")])
        self.assertEqual(result.days[0].routes[0].duration_s, 600)
        self.assertEqual(seen_review_summaries, [None, None])
        first_review_plan = reviewer.review_trip.await_args_list[0].args[1]
        self.assertIsNone(first_review_plan.review)
        prompt = json.loads(agent.return_value.run.call_args_list[1].args[0])
        self.assertEqual(prompt["review_feedback"]["issues"][0]["suggestion"], "减少一个景点。")
        self.assertEqual(len(prompt["previous_plan"]["days"][0]["routes"]), 2)
        self.assertEqual(maps.search_poi.await_count, 1)
        self.assertEqual([(call.args[0].origin_poi_id, call.args[0].destination_poi_id)
                          for call in maps.plan_route.await_args_list], [("a", "b"), ("b", "c"), ("a", "c")])

    async def test_revision_cannot_add_unverified_place(self):
        request, source, failed, _ = make_inputs()
        maps = Mock()
        maps.search_poi = AsyncMock(return_value=[POIInfo(id=key, name=f"真实{key}") for key in ("a", "b", "c")])
        maps.get_weather = AsyncMock(return_value=[])
        maps.search_hotels = AsyncMock(return_value=[])
        maps.search_restaurants = AsyncMock(return_value=[])
        maps.plan_route = AsyncMock(side_effect=AmapServiceError("no route"))
        planner = TripPlannerAgent(llm=Mock(), amap_service=maps)
        reviewer = Mock()
        reviewer.review_trip = AsyncMock(return_value=failed)
        revised = source.model_copy(deep=True)
        revised.days[0].attractions[0].poi_id = "invented"
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent:
            agent.return_value.run.side_effect = [source.model_dump_json(), revised.model_dump_json()]
            result = await TripPlanningWorkflow(planner, reviewer).plan_trip(request)
        self.assertEqual(result.review.status, "needs_revision")
        self.assertEqual(result.review.revisions, 0)
        self.assertEqual(result.days[0].attractions[0].poi_id, "a")


class WorkflowEndpointTests(unittest.TestCase):
    def test_opt_in_reaches_workflow_and_serializes_review(self):
        request, plan, _, passed = make_inputs()
        planner = Mock()
        planner.prepare_context = AsyncMock(return_value=object())
        planner.generate_plan = AsyncMock(return_value=plan)
        reviewer = Mock()
        reviewer.review_trip = AsyncMock(return_value=passed)
        with TestClient(app) as client, patch("app.api.main.TripPlannerAgent", return_value=planner), \
             patch("app.services.trip_workflow.TripReviewAgent", return_value=reviewer):
            response = client.post("/api/trip/plan", json=request.model_dump())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["review"]["status"], "passed")
        reviewer.review_trip.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
