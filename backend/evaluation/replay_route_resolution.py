"""离线重建历史结果的路线交付说明；不重新规划、不调用API、不覆盖原记录。"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from app.models.schemas import TripPlan, TripRequest
from app.services.trip_workflow import TripPlanningWorkflow
from evaluation.evaluators import evaluate_case
from evaluation.run_eval import EVALUATION_DIR, load_cases, local_source_hash


def transform_row(row, case, adaptation):
    expected = case.request.model_dump(mode="json")
    if adaptation == "expected_walking_limit_to_request" and case.expected.max_leg_walking_m is not None:
        expected["max_leg_walking_m"] = case.expected.max_leg_walking_m
    request = TripRequest.model_validate(row["request"])
    if request.model_dump(mode="json") != expected:
        raise ValueError("Historical request differs from the declared case/adaptation.")
    if row.get("error") or not row.get("result"):
        raise ValueError("Only delivered historical plans can be transformed.")
    case = case.model_copy(update={"request": request})
    original = TripPlan.model_validate(row["result"])
    plan = original.model_copy(deep=True)
    before = evaluate_case(case, original.model_dump(), row.get("candidate_evidence"))
    TripPlanningWorkflow._await_route_choice(request, plan)
    unchanged_fields = {"review", "route_resolution"}
    if original.model_dump(exclude=unchanged_fields) != plan.model_dump(exclude=unchanged_fields):
        raise ValueError("Route delivery transform unexpectedly changed original facts.")
    after = evaluate_case(case, plan.model_dump(), row.get("candidate_evidence"))
    if (before["checks"], before["hard_status"]) != (after["checks"], after["hard_status"]):
        raise ValueError("Route delivery transform changed original hard-rule grading.")
    return {
        "case_id": row["case_id"], "repetition": row["repetition"],
        "source_kind": "historical_response_transform", "original_source_kind": row.get("source_kind"),
        "request": request.model_dump(mode="json"), "http_status": None, "elapsed_s": None,
        "original_http_status": row.get("http_status"), "original_elapsed_s": row.get("elapsed_s"),
        "result": plan.model_dump(mode="json"), "candidate_evidence": row.get("candidate_evidence"),
        "evaluation_before": before, "evaluation": after,
        "facts_unchanged": True, "request_unchanged": True,
        "provenance": "No new HTTP/model/map request. Stage timings and source snapshots are historical; the route report and, when needed, program review summary are rebuilt.",
    }


def execute(source, output_root, label):
    source_meta = json.loads((source / "run.json").read_text(encoding="utf-8"))
    cases = {c.case_id: c for c in load_cases(EVALUATION_DIR / "cases.jsonl")}
    case_hash = hashlib.sha256((EVALUATION_DIR / "cases.jsonl").read_bytes()).hexdigest()
    if source_meta["cases_sha256"] != case_hash:
        raise ValueError("Dataset changed; do not silently reclassify the historical run.")
    raw = (source / "records.jsonl").read_bytes()
    originals = [json.loads(x) for x in raw.decode("utf-8").splitlines() if x]
    if not originals:
        raise ValueError("No historical records.")
    rows = [transform_row(row, cases[row["case_id"]], source_meta.get("request_adaptation")) for row in originals]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = output_root / f"{label}_{stamp}_{uuid4().hex[:8]}"
    output.mkdir(parents=True, exist_ok=False)
    metadata = {
        "mode": "historical_response_transform", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_run": str(source.resolve()), "source_records_sha256": hashlib.sha256(raw).hexdigest(),
        "original_local_source_sha256": source_meta["local_source_sha256"],
        "local_source_sha256": local_source_hash(), "cases_sha256": case_hash,
        "request_adaptation": source_meta.get("request_adaptation"), "live_calls": 0,
        "interpretation": "New route explanation contract only; original facts/hard grading retained. Not a new live test or a latency/cost comparison.",
    }
    summary = {
        "records": len(rows), "mode": metadata["mode"], "live_calls": 0,
        "hard_status_before": dict(Counter(r["evaluation_before"]["hard_status"] for r in rows)),
        "hard_status_after": dict(Counter(r["evaluation"]["hard_status"] for r in rows)),
        "route_resolution_handling": dict(Counter(r["evaluation"]["route_resolution_handling"]["status"] for r in rows)),
        "current_route_status": dict(Counter(r["result"]["route_resolution"]["status"] for r in rows)),
        "request_and_facts_unchanged": all(r["request_unchanged"] and r["facts_unchanged"] for r in rows),
        "latency_and_actual_cost": "not_measured_no_new_live_calls", "human_quality": "not_scored",
    }
    for name, value in (("run.json", metadata), ("summary.json", summary)):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "records.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    report = ["# 历史结果的路线交付说明回放", "", "这是离线重建，不是新一轮live。没有重新查路线或调用模型；原始记录保持不变。",
              "原硬约束结论与路线说明检查分开；说明合格不代表路线或整体旅行质量达标。", "",
              "| 样例 | 原硬约束 | 重建后硬约束 | 路线交付状态 | 说明检查 |", "| --- | --- | --- | --- | --- |"]
    for row in rows:
        resolution = row["result"]["route_resolution"]
        report.append(f"| {row['case_id']} | {row['evaluation_before']['hard_status']} | {row['evaluation']['hard_status']} | {resolution['status']} {resolution['verified_segments']}/{resolution['required_segments']} | {row['evaluation']['route_resolution_handling']['status']} |")
    report += ["", "请求、路线事实和来源快照均保留。没有新性能数据，不能将历史耗时当作本次耗时。", "",
               "原批次：" + str(source.resolve()), "当前业务与评测源码SHA256：" + metadata["local_source_sha256"], ""]
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("Saved offline transform:", output)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=EVALUATION_DIR / "results")
    parser.add_argument("--label", default="route_resolution_replay")
    args = parser.parse_args()
    if not args.label or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in args.label):
        parser.error("label must contain letters, digits, underscores or hyphens")
    execute(args.source_run, args.output_root, args.label)
