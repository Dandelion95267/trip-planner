"""单独验证交付说明，不能靠解释把不完整路线的原硬约束失败改为通过。"""

import unittest

from app.models.schemas import TripPlan
from app.services.route_resolution import build_route_resolution
from evaluation.evaluators import evaluate_case, summarize
from evaluation.route_resolution_checks import evaluate_route_resolution
from evaluation.replay_route_resolution import transform_row
from evaluation.test_evaluation import CASES, ELDER_ID, good_elderly


class ResolutionEvaluationTests(unittest.TestCase):
    def inputs(self):
        case = CASES[ELDER_ID].model_copy(deep=True)
        case.request.max_leg_walking_m = 1000
        row = good_elderly()
        plan = TripPlan.model_validate(row["result"])
        plan.days[0].routes = []
        plan.review = None
        plan.route_resolution = build_route_resolution(case.request, plan)
        return case, row, plan

    def test_honest_fallback_passes_its_own_check_but_hard_status_still_fails(self):
        case, row, plan = self.inputs()
        result = evaluate_case(case, plan.model_dump(), row.get("candidate_evidence"))
        self.assertEqual(result["hard_status"], "failed")
        self.assertEqual(result["route_resolution_handling"]["status"], "passed")
        self.assertEqual(result["routes"]["matched"], 1)

    def test_wrong_constraint_snapshot_is_rejected(self):
        case, _, plan = self.inputs()
        plan.route_resolution.max_leg_walking_m = 2000
        self.assertEqual(evaluate_route_resolution(case.request, plan)["status"], "failed")

    def test_stale_unresolved_pair_is_rejected(self):
        case, _, plan = self.inputs()
        plan.route_resolution.unresolved[0].destination_poi_id = "another-pair"
        self.assertEqual(evaluate_route_resolution(case.request, plan)["status"], "failed")

    def test_fabricated_reason_or_distance_is_rejected(self):
        case, _, plan = self.inputs()
        plan.route_resolution.unresolved[0].reason = "over_limit"
        plan.route_resolution.unresolved[0].observed_walking_m = 1800
        self.assertEqual(evaluate_route_resolution(case.request, plan)["status"], "failed")

    def test_fabricated_numeric_alternative_is_rejected(self):
        case, _, plan = self.inputs()
        option = next(x for x in plan.route_resolution.alternatives if x.kind == "relax_walking_limit")
        option.suggested_max_leg_walking_m = 1500
        self.assertEqual(evaluate_route_resolution(case.request, plan)["status"], "failed")

    def test_legacy_missing_report_is_not_backfilled_as_a_pass(self):
        case, _, plan = self.inputs()
        plan.route_resolution = None
        self.assertEqual(evaluate_route_resolution(case.request, plan)["status"], "not_evaluated")

    def test_walking_over_limit_route_is_not_verified_even_if_it_exists(self):
        case, row, plan = self.inputs()
        plan.days[0].routes = TripPlan.model_validate(row["result"]).days[0].routes
        plan.days[0].routes[0].walking_distance_m = 1600
        plan.route_resolution = build_route_resolution(case.request, plan)
        result = evaluate_case(case, plan.model_dump(), row.get("candidate_evidence"))
        self.assertEqual(result["hard_status"], "failed")
        self.assertEqual(result["route_resolution_handling"]["status"], "passed")
        self.assertEqual(plan.route_resolution.verified_segments, 1)

    def test_summary_keeps_both_metrics_separate(self):
        case, row, plan = self.inputs()
        result = evaluate_case(case, plan.model_dump(), row.get("candidate_evidence"))
        summary = summarize([{"evaluation": result, "elapsed_s": None}])
        self.assertEqual(summary["hard_status_counts"], {"failed": 1})
        self.assertEqual(summary["route_resolution_handling"], {"passed": 1})

    def test_offline_transform_retains_original_facts_and_grading_without_new_timing(self):
        case, row, plan = self.inputs()
        row = {**row, "request": case.request.model_dump(mode="json"), "result": plan.model_dump(mode="json")}
        result = transform_row(row, case, "none")
        self.assertTrue(result["facts_unchanged"])
        self.assertEqual(result["evaluation"]["checks"], result["evaluation_before"]["checks"])
        self.assertEqual(result["evaluation"]["hard_status"], "failed")
        self.assertIsNone(result["elapsed_s"])
        self.assertEqual(result["result"]["days"], plan.model_dump(mode="json")["days"])

    def test_offline_transform_rejects_unrecorded_constraint_changes(self):
        case, row, _ = self.inputs()
        row = {**row, "request": case.request.model_dump(mode="json")}
        row["request"]["max_leg_walking_m"] = 2000
        with self.assertRaisesRegex(ValueError, "differs"):
            transform_row(row, case, "none")
