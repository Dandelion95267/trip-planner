"""验证审核资料、结果解析及失败处理，不调用外部模型或地图服务。"""

import json
import threading
import unittest
from unittest.mock import Mock, patch

from app.agents.review_agent import TripReviewAgent, TripReviewError
from app.models.schemas import RoutePlan, TripPlan, TripRequest


def make_review_inputs():
    request = TripRequest(
        city="北京", start_date="2026-11-01", end_date="2026-11-01", travel_days=1,
        transportation="公共交通", accommodation="经济型酒店", free_text_input="带老人，少走路",
    )
    plan = TripPlan(
        city=request.city, start_date=request.start_date, end_date=request.end_date,
        days=[{
            "date": request.start_date, "day_index": 0, "description": "依次游览三个景点。",
            "transportation": request.transportation, "accommodation": request.accommodation,
            "attractions": [
                {"poi_id": place_id, "name": f"景点{place_id}", "address": "地图地址",
                 "visit_duration": 120, "description": "建议游览两小时。"}
                for place_id in ("a", "b", "c")
            ],
            "routes": [{
                "origin": {"id": "a", "name": "景点a"},
                "destination": {"id": "b", "name": "景点b"},
                "mode": "transit", "duration_s": 1800,
            }],
            "route_warnings": ["景点b到景点c的路线暂不可用。"],
        }],
        overall_suggestions="出发前确认预约要求。",
    )
    return request, plan


def make_failed_review(day_index=0):
    return {
        "passed": False, "summary": "建议减少第一天的游览强度。",
        "issues": [{
            "category": "pace", "day_index": day_index,
            "problem": "行程强度可能不符合带老人轻松游玩的需求。",
            "evidence": "用户要求少走路，当前安排三个各120分钟的景点，已知交通另需30分钟。",
            "suggestion": "减少一个景点，并保留休息时间。",
        }],
    }


class ReviewAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.request, self.plan = make_review_inputs()
        self.plan.days[0].routes.append(RoutePlan(
            origin={"id": "b", "name": "景点b"}, destination={"id": "c", "name": "景点c"},
            mode="transit", duration_s=1200,
        ))
        self.plan.days[0].route_warnings = []
        self.llm = Mock()
        self.reviewer = TripReviewAgent(llm=self.llm)
        patcher = patch("app.agents.review_agent.SimpleAgent")
        self.agent_class = patcher.start()
        self.addCleanup(patcher.stop)
        self.agent_class.return_value.run.return_value = json.dumps({
            "passed": True, "summary": "未发现待修改问题。", "issues": [],
        })

    def get_payload(self):
        return json.loads(self.agent_class.return_value.run.call_args.args[0])

    async def test_returns_structured_review_without_mutating_inputs(self):
        before_request = self.request.model_dump()
        before_plan = self.plan.model_dump()
        result = await self.reviewer.review_trip(self.request, self.plan)
        self.assertTrue(result.passed)
        self.assertEqual(result.issues, [])
        self.assertEqual(self.request.model_dump(), before_request)
        self.assertEqual(self.plan.model_dump(), before_plan)
        payload = self.get_payload()
        self.assertEqual(payload["request"]["free_text_input"], "带老人，少走路")
        self.assertEqual(payload["plan"]["days"][0]["route_warnings"], self.plan.days[0].route_warnings)

    async def test_partial_routes_are_not_presented_as_complete(self):
        self.plan.days[0].routes.pop()
        # 单独验证语义审核资料；公开入口会先拦截不完整路线。
        self.reviewer._review_trip(self.request, self.plan)
        evidence = self.get_payload()["daily_time_evidence"][0]
        self.assertEqual(evidence["suggested_visit_minutes"], 360)
        self.assertEqual(evidence["expected_route_count"], 2)
        self.assertEqual(evidence["known_route_count"], 1)
        self.assertEqual(evidence["known_route_duration_s"], 1800)
        self.assertFalse(evidence["all_adjacent_routes_available"])

    async def test_counts_only_routes_matching_current_attraction_order(self):
        self.plan.days[0].routes.pop()
        self.plan.days[0].routes.append(RoutePlan(
            origin={"id": "c", "name": "景点c"}, destination={"id": "a", "name": "景点a"},
            mode="walking", duration_s=9999,
        ))
        self.reviewer._review_trip(self.request, self.plan)
        evidence = self.get_payload()["daily_time_evidence"][0]
        self.assertEqual(evidence["known_route_count"], 1)
        self.assertEqual(evidence["known_route_duration_s"], 1800)

    async def test_preserves_actionable_failed_review(self):
        self.agent_class.return_value.run.return_value = json.dumps(make_failed_review())
        result = await self.reviewer.review_trip(self.request, self.plan)
        self.assertFalse(result.passed)
        self.assertEqual(result.issues[0].category, "pace")
        self.assertEqual(result.issues[0].day_index, 0)

    async def test_accepts_whole_trip_issue(self):
        self.agent_class.return_value.run.return_value = json.dumps(make_failed_review(None))
        result = await self.reviewer.review_trip(self.request, self.plan)
        self.assertIsNone(result.issues[0].day_index)

    async def test_rejects_day_not_in_current_trip(self):
        self.agent_class.return_value.run.return_value = json.dumps(make_failed_review(1))
        with self.assertRaisesRegex(TripReviewError, "不存在的天数"):
            await self.reviewer.review_trip(self.request, self.plan)

    async def test_rejects_malformed_or_contradictory_model_output(self):
        contradictory = make_failed_review()
        contradictory["passed"] = True
        missing_evidence = make_failed_review()
        missing_evidence["issues"][0]["evidence"] = "   "
        responses = [None, "", "not json", "[]", json.dumps(contradictory),
                     json.dumps({"passed": False, "summary": "修改", "issues": []}),
                     json.dumps(missing_evidence)]
        for response in responses:
            with self.subTest(response=response):
                self.agent_class.return_value.run.return_value = response
                with self.assertRaisesRegex(TripReviewError, "格式不完整"):
                    await self.reviewer.review_trip(self.request, self.plan)

    async def test_model_failure_is_not_a_pass_and_does_not_expose_raw_error(self):
        self.agent_class.return_value.run.side_effect = RuntimeError("private upstream details")
        with self.assertRaises(TripReviewError) as caught:
            await self.reviewer.review_trip(self.request, self.plan)
        self.assertNotIn("private upstream details", str(caught.exception))
        self.assertIn("暂时无法审核", str(caught.exception))

    async def test_mismatched_input_is_rejected_before_model_call(self):
        bad_plans = [
            self.plan.model_copy(update={"city": "上海"}),
            self.plan.model_copy(update={"days": []}),
            self.plan.model_copy(deep=True),
        ]
        bad_plans[-1].days[0].date = "2026-11-02"
        for plan in bad_plans:
            with self.subTest(plan=plan):
                with self.assertRaisesRegex(TripReviewError, "不一致"):
                    await self.reviewer.review_trip(self.request, plan)
        self.agent_class.assert_not_called()

    async def test_fresh_agent_per_review_and_model_runs_off_event_loop(self):
        main_thread = threading.get_ident()
        worker_threads = []
        response = self.agent_class.return_value.run.return_value

        def run_model(*args, **kwargs):
            worker_threads.append(threading.get_ident())
            return response

        self.agent_class.return_value.run.side_effect = run_model
        await self.reviewer.review_trip(self.request, self.plan)
        await self.reviewer.review_trip(self.request, self.plan)
        self.assertEqual(self.agent_class.call_count, 2)
        self.assertTrue(all(worker != main_thread for worker in worker_threads))
        self.assertIs(self.agent_class.call_args.kwargs["llm"], self.llm)
        self.assertFalse(self.agent_class.call_args.kwargs["enable_tool_calling"])


if __name__ == "__main__":
    unittest.main()
