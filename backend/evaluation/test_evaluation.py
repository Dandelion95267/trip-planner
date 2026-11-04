"""Check scoring and execution with synthetic records/HTTP mocks; no paid APIs."""

from copy import deepcopy
import asyncio
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from evaluation.evaluators import evaluate_case, summarize
from evaluation.run_eval import attach_evaluation, load_replay, main, replay_case, run_http_case, select_cases
from evaluation.validate_cases import load_cases


ROOT = Path(__file__).resolve().parent
CASES = {case.case_id: case for case in load_cases(ROOT / "cases.jsonl")}
EXAMPLES = load_replay(ROOT / "examples/synthetic_responses.jsonl")
GOOD_ID = "dev_basic_beijing_2d"
ELDER_ID = "dev_transport_beijing_elderly"


def example(case_id=GOOD_ID):
    return deepcopy(EXAMPLES[(case_id, 1)])


def grade(row):
    return evaluate_case(CASES[row["case_id"]], row["result"], row.get("candidate_evidence"))


def good_elderly():
    row = example(ELDER_ID)
    for day in row["result"]["days"]:
        for route in day["routes"]:
            route["walking_distance_m"] = 500
    return row


class EvaluatorTests(unittest.TestCase):
    def test_complete_synthetic_evidence_can_pass_without_scoring_subjective_quality(self):
        result = grade(example())
        self.assertEqual(result["hard_status"], "passed")
        self.assertEqual(result["manual_review"], "pending")
        self.assertEqual(result["routes"], {"expected": 2, "matched": 2})

    def test_count_failure_is_not_hidden_by_self_reported_review_pass(self):
        result = grade(example("dev_count_hangzhou_max2"))
        self.assertEqual(result["reported_review_status"], "passed")
        self.assertEqual(result["checks"]["daily_count"]["status"], "failed")
        self.assertEqual(result["hard_status"], "failed")

    def test_missing_source_evidence_is_unknown_not_passed(self):
        row = example()
        row["candidate_evidence"] = None
        result = grade(row)
        self.assertTrue(result["available_checks_pass"])
        self.assertEqual(result["hard_status"], "unknown")

    def test_unknown_candidate_is_rejected(self):
        row = example()
        row["candidate_evidence"]["candidates"] = []
        self.assertEqual(grade(row)["checks"]["candidate_sources"]["status"], "failed")

    def test_supplemented_coordinates_need_additional_evidence(self):
        row = example()
        row["result"]["days"][0]["attractions"][0]["location"] = {"longitude": 116.4, "latitude": 39.9}
        self.assertEqual(grade(row)["checks"]["candidate_sources"]["status"], "unknown")

    def test_empty_days_are_not_counted_as_delivered(self):
        row = example()
        row["result"]["days"] = []
        self.assertFalse(grade(row)["delivered"])

    def test_wrong_date_fails_even_with_a_valid_schema(self):
        row = example()
        row["result"]["days"][0]["date"] = "2026-12-01"
        self.assertEqual(grade(row)["checks"]["dates"]["status"], "failed")

    def test_stale_route_is_not_counted_as_a_current_route(self):
        row = example()
        route = row["result"]["days"][0]["routes"][0]
        route["origin"], route["destination"] = route["destination"], route["origin"]
        result = grade(row)
        self.assertEqual(result["checks"]["route_integrity"]["status"], "failed")
        self.assertEqual(result["routes"], {"expected": 2, "matched": 1})

    def test_long_walking_fails(self):
        result = grade(example(ELDER_ID))
        self.assertEqual(result["checks"]["walking_limit"]["status"], "failed")

    def test_missing_walking_data_is_not_zero(self):
        row = good_elderly()
        row["result"]["days"][0]["routes"][0]["walking_distance_m"] = None
        result = grade(row)
        self.assertEqual(result["checks"]["walking_limit"]["status"], "unknown")
        self.assertEqual(result["hard_status"], "unknown")

    def test_missing_required_route_fails_completeness(self):
        row = good_elderly()
        row["result"]["days"][0]["routes"] = []
        result = grade(row)
        self.assertEqual(result["checks"]["route_completeness"]["status"], "failed")
        self.assertEqual(result["checks"]["walking_limit"]["status"], "unknown")

    def test_wrong_transport_mode_fails(self):
        row = good_elderly()
        route = row["result"]["days"][0]["routes"][0]
        route.update(mode="driving", distance_m=1000)
        self.assertEqual(grade(row)["checks"]["route_modes"]["status"], "failed")

    def test_budget_is_independently_checked(self):
        row = example()
        self.assertEqual(row["result"]["budget"]["total"], 1220)
        row["result"]["budget"]["total"] = 1221
        self.assertEqual(grade(row)["checks"]["budget"]["status"], "failed")

    def test_budget_item_quantity_is_checked_even_when_totals_are_correct(self):
        row = example()
        row["result"]["budget"]["items"][0]["quantity"] = 7
        self.assertEqual(grade(row)["checks"]["budget"]["status"], "failed")

    def test_unknown_budget_is_preserved_and_not_replaced_by_zero(self):
        row = good_elderly()
        self.assertEqual(grade(row)["checks"]["budget"]["status"], "passed")
        row["result"]["budget"]["total_attractions"] = 0
        self.assertEqual(grade(row)["checks"]["budget"]["status"], "failed")

    def test_failures_remain_in_rate_denominators(self):
        good = attach_evaluation(CASES[GOOD_ID], 1, replay_case(CASES[GOOD_ID], 1, EXAMPLES))
        missing = attach_evaluation(CASES[GOOD_ID], 2, replay_case(CASES[GOOD_ID], 2, EXAMPLES))
        report = summarize([good, missing])
        self.assertEqual(report["runs"], 2)
        self.assertEqual(report["delivery_rate"], 0.5)
        self.assertEqual(report["verified_hard_pass_rate"], 0.5)
        self.assertEqual(report["elapsed_s"]["count"], 0)


class HttpExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, handler):
        async with httpx.AsyncClient(base_url="http://mock.invalid", transport=httpx.MockTransport(handler)) as client:
            return await run_http_case(client, CASES[GOOD_ID], 1, 1)

    async def test_http_sends_only_request_not_expected_answers(self):
        def handler(request):
            payload = json.loads(request.content)
            self.assertEqual(payload, CASES[GOOD_ID].request.model_dump(mode="json"))
            self.assertNotIn("expected", payload)
            return httpx.Response(200, json=example()["result"])
        record = await self.call(handler)
        self.assertIsNone(record["error"])
        self.assertIsNone(record["candidate_evidence"])

    async def test_http_error_is_recorded_without_raw_private_details(self):
        record = await self.call(lambda request: httpx.Response(502, json={"detail": "private provider details"}))
        self.assertEqual(record["error"]["type"], "http_error")
        self.assertNotIn("private provider", json.dumps(record))

    async def test_timeout_is_recorded(self):
        def handler(request):
            raise httpx.ReadTimeout("private upstream details", request=request)
        record = await self.call(handler)
        self.assertEqual(record["error"]["type"], "timeout")
        self.assertIsNone(record["result"])

    async def test_non_json_success_is_recorded_as_invalid_json(self):
        record = await self.call(lambda request: httpx.Response(200, text="not JSON"))
        self.assertEqual(record["error"]["type"], "invalid_json")

    async def test_nonfinite_json_is_recorded_instead_of_breaking_result_storage(self):
        for text in ('{"value": NaN}', '{"value": 1e400}'):
            record = await self.call(lambda request: httpx.Response(200, text=text))
            self.assertEqual(record["error"]["type"], "invalid_json")

    async def test_outer_waiting_budget_is_enforced(self):
        async def handler(request):
            await asyncio.sleep(0.1)
            return httpx.Response(200, json=example()["result"])
        async with httpx.AsyncClient(base_url="http://mock.invalid", transport=httpx.MockTransport(handler)) as client:
            record = await run_http_case(client, CASES[GOOD_ID], 1, 0.001)
        self.assertEqual(record["error"]["type"], "timeout")


class RunnerTests(unittest.TestCase):
    def test_walking_limit_adapter_is_explicit_and_does_not_change_v0_cases(self):
        args = SimpleNamespace(split="development", pilot=False, case_id=[ELDER_ID], limit=None, apply_expected_walking_limit=True)
        selected = select_cases(list(CASES.values()), args)
        self.assertEqual(selected[0].request.max_leg_walking_m, 1000)
        self.assertIsNone(CASES[ELDER_ID].request.max_leg_walking_m)
        self.assertEqual(replay_case(selected[0], 1, EXAMPLES)["error"]["type"], "replay_request_mismatch")

    def test_preview_does_not_construct_a_client_or_write_results(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            output = Path(temp) / "results"
            with patch("httpx.AsyncClient", side_effect=AssertionError("preview must not create an HTTP client")), patch("sys.stdout", new_callable=StringIO):
                code = main(["--pilot", "--output-root", str(output)])
            self.assertEqual(code, 0)
            self.assertFalse(output.exists())

    def test_replay_rejects_request_mismatch(self):
        changed = deepcopy(EXAMPLES)
        changed[(GOOD_ID, 1)]["request"]["free_text_input"] = "different requirement"
        record = replay_case(CASES[GOOD_ID], 1, changed)
        self.assertEqual(record["error"]["type"], "replay_request_mismatch")

    def test_synthetic_replay_writes_honest_reports_without_posting(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=AssertionError("replay must not post")), patch("sys.stdout", new_callable=StringIO):
                code = main(["--pilot", "--replay", str(ROOT / "examples/synthetic_responses.jsonl"), "--output-root", temp, "--label", "synthetic_test"])
            self.assertEqual(code, 0)
            report_dir = next(Path(temp).iterdir())
            summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["runs"], 5)
            self.assertEqual(summary["delivered"], 4)
            self.assertEqual(summary["hard_status_counts"], {"passed": 1, "failed": 3, "unknown": 1})
            self.assertEqual(summary["source_kind_counts"], {"synthetic": 5})
            self.assertEqual(summary["mode"], "replay")
            self.assertEqual(summary["manual_quality"], "not_scored")
            self.assertEqual(summary["actual_cost"], "not_collected")
            self.assertEqual(summary["elapsed_s"]["count"], 0)
            self.assertEqual(len((report_dir / "records.jsonl").read_text(encoding="utf-8").splitlines()), 5)
            self.assertEqual(len((report_dir / "manual_review.csv").read_text(encoding="utf-8-sig").splitlines()), 16)
            self.assertTrue((report_dir / "report.md").exists())


if __name__ == "__main__":
    unittest.main()
