"""审核程序兜底的实际故障回归：错名不可放行，时间不由模型加总。"""

import json
import unittest
from unittest.mock import Mock, patch

from app.agents.review_agent import TripReviewAgent
from app.models.schemas import CandidateEvidence, Meal, POIInfo, TripPlan, TripRequest
from app.services.review_checks import build_time_evidence, check_meal_names, compose_review


def inputs():
    request = TripRequest(city="模拟城市", start_date="2026-11-05", end_date="2026-11-05", travel_days=1,
        transportation="步行", route_mode="walking", accommodation="不住宿", enable_review=True)
    plan = TripPlan(city=request.city, start_date=request.start_date, end_date=request.end_date,
        overall_suggestions="虚构测试样例，实际接驳与营业未知。", days=[{
            "date": request.start_date, "day_index": 0, "transportation": "步行", "accommodation": "不住宿",
            "description": "上午游览甲景点；之后在甲景点附近的餐馆甲午餐，午餐后休息；下午前往乙景点。",
            "attractions": [{"poi_id": name, "name": name, "address": "模拟地址", "visit_duration": minutes, "description": "休闲参观。"} for name, minutes in [("甲景点", 30), ("乙景点", 45)]],
            "meals": [{"poi_id": "meal_a", "type": "lunch", "name": "餐馆甲", "anchor_poi_id": "甲景点"}],
            "routes": [{"origin": {"id": "甲景点", "name": "甲景点"}, "destination": {"id": "乙景点", "name": "乙景点"}, "mode": "walking", "distance_m": 500, "duration_s": 600}],
        }])
    return request, plan


class ReviewChecksTests(unittest.TestCase):
    def setUp(self):
        self.request, self.plan = inputs()

    def test_known_time_uses_seconds_and_has_85_minute_display(self):
        item = build_time_evidence(self.plan)[0]
        self.assertEqual(item.known_total_duration_s, 5100)
        self.assertIn("已知合计85分钟", item.display_text)
        self.assertIn("不是完整全天耗时", item.display_text)
        self.assertIn("未计入用餐、休息", item.display_text)

    def test_missing_route_stays_partial_not_zero_time_full_day(self):
        self.plan.days[0].routes = []
        item = build_time_evidence(self.plan)[0]
        self.assertEqual(item.known_total_duration_s, 4500)
        self.assertFalse(item.all_adjacent_routes_available)
        self.assertIn("仅有0/1段", item.display_text)
        self.assertIn("缺失耗时未知", item.display_text)

    def test_seconds_not_rounded_and_irrelevant_edges_not_counted(self):
        route = self.plan.days[0].routes[0]
        route.duration_s = 61
        extra = route.model_copy(deep=True)
        extra.origin.id = "其他景点"
        extra.duration_s = 99999
        self.plan.days[0].routes.append(extra)
        item = build_time_evidence(self.plan)[0]
        self.assertEqual(item.known_total_duration_s, 4561)
        self.assertIn("已知合计76分1秒", item.display_text)

    def test_single_attraction_has_no_missing_edge(self):
        self.plan.days[0].attractions.pop()
        item = build_time_evidence(self.plan)[0]
        self.assertEqual(item.known_route_count, 0)
        self.assertTrue(item.all_adjacent_routes_available)

    def test_normal_morning_lunch_afternoon_sequence_is_not_conflict(self):
        checks, issues = check_meal_names(self.plan)
        self.assertEqual(checks[0].status, "matched")
        self.assertEqual(issues, [])

    def test_explicit_different_restaurant_has_two_field_evidence(self):
        self.plan.days[0].description = self.plan.days[0].description.replace("餐馆甲午餐", "餐馆乙午餐")
        checks, issues = check_meal_names(self.plan)
        self.assertEqual(checks[0].status, "conflict")
        self.assertEqual(len(issues), 1)
        self.assertIn("餐馆乙", issues[0].evidence)
        self.assertIn("餐馆甲", issues[0].evidence)
        self.assertEqual(issues[0].source, "program")

    def test_colon_format_and_parentheses_normalized(self):
        self.plan.days[0].meals[0].name = "甲菜馆（中心店）"
        self.plan.days[0].description = "午餐候选：甲菜馆(中心店)，接驳未核实。"
        self.assertEqual(check_meal_names(self.plan)[0][0].status, "matched")

    def test_short_name_or_brand_alias_is_unknown_not_conflict(self):
        self.plan.days[0].meals[0].name = "刘妈菜馆(中心店)"
        self.plan.days[0].description = "午餐候选：刘妈。"
        checks, issues = check_meal_names(self.plan)
        self.assertEqual(checks[0].status, "unknown")
        self.assertEqual(issues, [])

    def test_similar_name_without_source_id_is_not_forced_different(self):
        self.plan.days[0].meals[0].name = "同心餐厅(东店)"
        self.plan.days[0].description = "午餐候选：同心餐厅(西店)。"
        self.assertEqual(check_meal_names(self.plan)[0][0].status, "unknown")

    def test_absent_ambiguous_negative_or_alternative_names_are_unknown(self):
        for text in ["午餐自行安排。", "午餐不去餐馆乙。", "午餐：餐馆乙或餐馆甲。", "午餐：餐馆甲。午餐：餐馆乙。", "午餐后前往乙景点。",
                     "午餐：餐馆乙（若营业）。", "如果有空午餐去餐馆乙。", "原计划午餐：餐馆乙。", "午餐可以去餐馆乙。"]:
            with self.subTest(text=text):
                self.plan.days[0].description = text
                checks, issues = check_meal_names(self.plan)
                self.assertEqual(checks[0].status, "unknown")
                self.assertEqual(issues, [])

    def test_captured_alias_with_same_poi_id_is_matched(self):
        self.plan.candidate_evidence = CandidateEvidence(request_id="synthetic", restaurant_candidates=[POIInfo(id="meal_a", name="别名餐厅")])
        self.plan.days[0].description = "午餐候选：别名餐厅。"
        self.assertEqual(check_meal_names(self.plan)[0][0].status, "matched")

    def test_distinct_captured_id_conflicts_even_similar_brand_name(self):
        self.plan.days[0].meals[0].name = "同心餐厅(东店)"
        self.plan.candidate_evidence = CandidateEvidence(request_id="synthetic", restaurant_candidates=[POIInfo(id="meal_b", name="同心餐厅(西店)")])
        self.plan.days[0].description = "午餐候选：同心餐厅(西店)。"
        self.assertEqual(check_meal_names(self.plan)[0][0].status, "conflict")

    def test_dinner_does_not_compare_with_lunch(self):
        self.plan.days[0].description = "午餐：餐馆甲。晚餐：餐馆乙。"
        self.plan.days[0].meals.append(Meal(type="dinner", name="餐馆乙"))
        checks, issues = check_meal_names(self.plan)
        self.assertEqual([c.status for c in checks], ["matched", "matched"])
        self.assertEqual(issues, [])

    def test_incomplete_semantic_review_cannot_be_fabricated_pass(self):
        with self.assertRaises(ValueError):
            compose_review(None, [], [], [], "unavailable")


class ReviewGuardIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.request, self.plan = inputs()
        self.reviewer = TripReviewAgent(llm=Mock())
        patcher = patch("app.agents.review_agent.SimpleAgent")
        self.agent = patcher.start()
        self.addCleanup(patcher.stop)
        self.agent.return_value.run.return_value = json.dumps({"passed": True, "summary": "已知合计22.5分钟。", "issues": []})

    async def test_model_pass_cannot_override_program_name_conflict(self):
        self.plan.days[0].description = self.plan.days[0].description.replace("餐馆甲午餐", "餐馆乙午餐")
        result = await self.reviewer.review_trip(self.request, self.plan)
        self.assertFalse(result.passed)
        self.assertTrue(any(i.source == "program" and "餐馆乙" in i.evidence for i in result.issues))
        self.assertEqual(result.semantic_status, "completed")

    async def test_bad_arithmetic_summary_is_not_published(self):
        before = self.plan.model_dump()
        result = await self.reviewer.review_trip(self.request, self.plan)
        self.assertTrue(result.passed)
        self.assertNotIn("22.5", result.summary)
        self.assertEqual(result.time_evidence[0].known_total_duration_s, 5100)
        self.assertIn("85分钟", result.time_evidence[0].display_text)
        self.assertEqual(self.plan.model_dump(), before)

    async def test_forged_time_name_checks_and_source_do_not_override_program(self):
        self.agent.return_value.run.return_value = json.dumps({"passed": False, "summary": "模型意见", "issues": [{"category": "pace", "problem": "需要复核", "evidence": "模拟依据", "suggestion": "复核", "source": "program"}],
            "time_evidence": [{"known_total_duration_s": 99999}], "meal_consistency": "forged", "semantic_status": "not_run"})
        result = await self.reviewer.review_trip(self.request, self.plan)
        self.assertEqual(result.time_evidence[0].known_total_duration_s, 5100)
        self.assertEqual(result.meal_consistency[0].status, "matched")
        self.assertEqual(result.issues[0].source, "model")
        self.assertEqual(result.semantic_status, "completed")
        payload = json.loads(self.agent.return_value.run.call_args.args[0])
        self.assertNotIn("time_evidence", payload["output_schema"]["properties"])
        self.assertEqual(payload["daily_time_evidence"][0]["known_total_duration_s"], 5100)

    async def test_known_conflict_survives_provider_failure(self):
        self.plan.days[0].description = "午餐：餐馆乙。"
        self.agent.return_value.run.side_effect = RuntimeError("private upstream data")
        result = await self.reviewer.review_trip(self.request, self.plan)
        self.assertFalse(result.passed)
        self.assertEqual(result.semantic_status, "unavailable")
        self.assertNotIn("private", result.model_dump_json())

    async def test_known_conflict_survives_malformed_review(self):
        self.plan.days[0].description = "午餐：餐馆乙。"
        self.agent.return_value.run.return_value = "not json"
        result = await self.reviewer.review_trip(self.request, self.plan)
        self.assertFalse(result.passed)
        self.assertEqual(result.semantic_status, "unavailable")

    async def test_route_precheck_keeps_time_partial_and_skips_semantic_model(self):
        self.plan.days[0].routes = []
        result = await self.reviewer.review_trip(self.request, self.plan)
        self.assertFalse(result.passed)
        self.assertEqual(result.semantic_status, "not_run")
        self.assertFalse(result.time_evidence[0].all_adjacent_routes_available)
        self.agent.assert_not_called()


if __name__ == "__main__":
    unittest.main()
