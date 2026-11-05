"""Preview an evaluation batch, replay saved responses, or explicitly call the API."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import sys
from time import perf_counter
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import ValidationError


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.schemas import TripRequest
from evaluation.evaluators import evaluate_case, summarize
from evaluation.validate_cases import EvaluationCase, load_cases, validate_manifest


EVALUATION_DIR = Path(__file__).resolve().parent


def reject_nonfinite_constant(value: str):
    raise ValueError("Non-finite JSON numbers are unsupported.")


def finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON numbers are unsupported.")
    return number


def write_json(path: Path, data) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def local_source_hash() -> str:
    digest = hashlib.sha256()
    paths = sorted((BACKEND_DIR / "app").rglob("*.py")) + sorted(EVALUATION_DIR.glob("*.py"))
    for path in paths:
        digest.update(path.relative_to(BACKEND_DIR).as_posix().encode())
        digest.update(b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def select_cases(cases: list[EvaluationCase], args) -> list[EvaluationCase]:
    selected = [case for case in cases if args.split == "all" or case.split == args.split]
    if args.pilot:
        selected = [case for case in selected if case.pilot]
    if args.case_id:
        wanted = set(args.case_id)
        if wanted - {case.case_id for case in selected}:
            raise ValueError("Some requested IDs do not belong to the selected split/pilot scope.")
        selected = [case for case in selected if case.case_id in wanted]
    if args.limit is not None:
        selected = selected[:args.limit]
    if not selected:
        raise ValueError("No cases match the selection.")
    if args.apply_expected_walking_limit:
        adapted = []
        for case in selected:
            request = case.request.model_dump()
            if case.expected.max_leg_walking_m is not None:
                request["max_leg_walking_m"] = case.expected.max_leg_walking_m
            adapted.append(case.model_copy(update={"request": TripRequest.model_validate(request)}))
        selected = adapted
    return selected


def load_replay(path: Path) -> dict[tuple[str, int], dict]:
    records = {}
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line, parse_constant=reject_nonfinite_constant, parse_float=finite_float)
        except ValueError as error:
            raise ValueError(f"Replay line {number} is not valid JSON.") from error
        if not isinstance(row, dict) or not isinstance(row.get("case_id"), str):
            raise ValueError(f"Replay line {number} has no case ID.")
        repetition = row.get("repetition", 1)
        if type(repetition) is not int or repetition < 1:
            raise ValueError(f"Replay line {number} has an invalid repetition.")
        key = (row["case_id"], repetition)
        if key in records:
            raise ValueError("Replay has duplicate case/repetition records.")
        records[key] = row
    return records


async def run_http_case(client: httpx.AsyncClient, case: EvaluationCase, repetition: int, timeout_s: float) -> dict:
    started = perf_counter()
    record = {"source_kind": "live", "http_status": None, "result": None, "error": None, "candidate_evidence": None}
    try:
        async with asyncio.timeout(timeout_s):
            response = await client.post("/api/trip/plan", json=case.request.model_dump(mode="json"))
        record["http_status"] = response.status_code
        if not response.is_success:
            record["error"] = {"type": "http_error", "status_code": response.status_code}
        else:
            try:
                record["result"] = response.json(parse_constant=reject_nonfinite_constant, parse_float=finite_float)
                if isinstance(record["result"], dict):
                    captured = record["result"].get("candidate_evidence")
                    if isinstance(captured, dict):
                        record["candidate_evidence"] = captured
            except ValueError:
                record["error"] = {"type": "invalid_json"}
    except (TimeoutError, httpx.TimeoutException):
        record["error"] = {"type": "timeout"}
    except httpx.HTTPError:
        # Raw URL/network errors may contain sensitive query strings.
        record["error"] = {"type": "network_error"}
    record["elapsed_s"] = round(perf_counter() - started, 4)
    return record


def replay_case(case: EvaluationCase, repetition: int, inputs: dict) -> dict:
    row = inputs.get((case.case_id, repetition))
    record = {"source_kind": "unverified_replay", "http_status": None, "result": None, "error": None, "candidate_evidence": None, "elapsed_s": None}
    if row is None:
        record["error"] = {"type": "replay_missing"}
        return record
    try:
        raw_request = row.get("request")
        if not isinstance(raw_request, dict) or set(raw_request) - set(TripRequest.model_fields):
            raise ValueError("invalid replay request")
        saved_request = TripRequest.model_validate(raw_request)
        if saved_request.model_dump() != case.request.model_dump():
            raise ValueError("request mismatch")
    except (ValueError, TypeError, ValidationError):
        record["error"] = {"type": "replay_request_mismatch"}
        return record
    record["source_kind"] = row.get("source_kind") if row.get("source_kind") in {"synthetic", "live"} else "unverified_replay"
    status = row.get("http_status")
    if type(status) is not int or not 100 <= status <= 599:
        record["error"] = {"type": "replay_invalid_status"}
        return record
    record["http_status"] = status
    elapsed = row.get("elapsed_s")
    if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and math.isfinite(elapsed) and elapsed >= 0:
        record["elapsed_s"] = elapsed
    if row.get("error") is not None or not 200 <= status < 300:
        record["error"] = {"type": "replayed_error"}
    else:
        record["result"] = row.get("result")
        record["candidate_evidence"] = row.get("candidate_evidence")
    return record


def attach_evaluation(case: EvaluationCase, repetition: int, result: dict) -> dict:
    usable = result["result"] if result.get("error") is None else None
    return {
        "case_id": case.case_id, "repetition": repetition, "split": case.split,
        "category": case.category, "request": case.request.model_dump(mode="json"),
        "expected": case.expected.model_dump(), "manual_checks": case.manual_checks,
        **result, "evaluation": evaluate_case(case, usable, result.get("candidate_evidence")),
    }


def write_reports(out_dir: Path, metadata: dict, records: list[dict]) -> dict:
    summary = summarize(records)
    summary["mode"] = metadata["mode"]
    summary["source_kind_counts"] = dict(Counter(row["source_kind"] for row in records))
    summary["planned_runs"] = metadata["planned_runs"]
    write_json(out_dir / "summary.json", summary)
    with (out_dir / "manual_review.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["case_id", "repetition", "dimension", "status", "score", "reviewer", "evidence", "notes"])
        writer.writeheader()
        for row in records:
            for dimension in ("preference", "pace", "consistency"):
                writer.writerow({"case_id": row["case_id"], "repetition": row["repetition"], "dimension": dimension, "status": row["evaluation"]["manual_review"]})
    rows = [
        "# 评测运行报告", "", f"运行模式：**{metadata['mode']}**；来源计数：`{summary['source_kind_counts']}`。",
        "", "replay为已有记录的重新评分；synthetic是模拟数据，不能作为真实模型基线。",
        "", "| 指标 | 结果 |", "| --- | --- |",
        f"| 尝试运行数 | {summary['runs']} |",
        f"| 行程交付 | {summary['delivered']}/{summary['runs']} |",
        f"| 已验证自动硬约束通过 | {summary['verified_hard_passes']}/{summary['runs']} |",
        f"| 已检查项未发现失败（允许未知） | {summary['available_checks_passes']}/{summary['runs']} |",
        f"| 自动结论 | `{summary['hard_status_counts']}` |",
        f"| 已交付行程相邻路线覆盖 | `{summary['route_coverage_on_delivered_plans']}` |",
        f"| 记录中的耗时（秒） | `{summary['elapsed_s']}` |",
        "", "未知证据不算已验证通过。路线覆盖只基于已交付行程，应结合交付率查看。",
        "HTTP模式保存后端提供的同次来源快照；缺少快照或坐标补全依据时仍为unknown，不能视为通过。旧记录不事后补证据。",
        "人工偏好、节奏和一致性尚未评分，请填写manual_review.csv并查阅rubric.md；本脚本尚不汇总人工评分。",
        "token、实际费用和完整模型/工具调用量未采集，不从review_count推算。",
        "本地代码哈希不证明远端服务器已加载同一版本；模型标签是运行者填写，需自行核对。",
        "路线待选择说明单独检查覆盖、原因、原条件和确认要求，不把解释清楚等同于路线已达标；旧记录不补造该说明。",
        "餐饮说明单独检查当前景点锚点、半径、时段覆盖与待确认选项；不证明菜单或口味已满足。",
        "偏好证据单独核对同次类别/名称引用与未知做法，不将其当成菜单或整体偏好已满足。",
        "", "## 逐条自动结论", "", "| 样例 | 重复 | 交付 | 原硬约束 | 路线交付说明 | 餐饮交付说明 | 偏好证据 |", "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    rows.extend(f"| {row['case_id']} | {row['repetition']} | {row['evaluation']['delivered']} | {row['evaluation']['hard_status']} | {row['evaluation'].get('route_resolution_handling', {}).get('status', 'not_evaluated')} | {row['evaluation'].get('dining_resolution_handling', {}).get('status', 'not_evaluated')} | {row['evaluation'].get('dining_preference_handling', {}).get('status', 'not_evaluated')} |" for row in records)
    (out_dir / "report.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return summary


async def execute(args, selected: list[EvaluationCase], metadata: dict, inputs: dict | None) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.output_root / f"{args.label}_{stamp}_{uuid4().hex[:8]}"
    out_dir.mkdir(parents=True, exist_ok=False)
    write_json(out_dir / "run.json", metadata)
    records = []
    # Sequential execution bounds paid calls; failures are saved, not dropped.
    async with httpx.AsyncClient(base_url=args.base_url, timeout=args.timeout, trust_env=False, follow_redirects=False) as client:
        with (out_dir / "records.jsonl").open("x", encoding="utf-8") as stream:
            for case in selected:
                for repetition in range(1, args.repeats + 1):
                    result = await run_http_case(client, case, repetition, args.timeout) if args.live else replay_case(case, repetition, inputs or {})
                    row = attach_evaluation(case, repetition, result)
                    records.append(row)
                    stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                    stream.flush()
                    print(f"{case.case_id} [{repetition}] delivered={row['evaluation']['delivered']} hard_status={row['evaluation']['hard_status']}", flush=True)
    write_reports(out_dir, metadata, records)
    return out_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=EVALUATION_DIR / "cases.jsonl")
    parser.add_argument("--manifest", type=Path, default=EVALUATION_DIR / "manifest.json")
    parser.add_argument("--split", choices=["development", "holdout", "all"], default="development")
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--apply-expected-walking-limit", action="store_true", help="Explicitly send numeric walking limits for V1; record this request adaptation.")
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--label", default="v0_current")
    parser.add_argument("--model-label", default="not_recorded")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=660)
    parser.add_argument("--output-root", type=Path, default=EVALUATION_DIR / "results")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Call the backend, which may use paid model/map APIs.")
    mode.add_argument("--replay", type=Path, help="Rescore saved JSONL records without calling APIs.")
    args = parser.parse_args(argv)
    if args.repeats < 1 or (args.limit is not None and args.limit < 1):
        parser.error("repeats and limit must be positive")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be positive and finite")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.label):
        parser.error("label must contain 1-64 letters, digits, underscores or hyphens")
    try:
        url = urlsplit(args.base_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
            raise ValueError("base-url must be an HTTP(S) origin without credentials, path or query")
        cases = load_cases(args.cases)
        manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be an object")
        validate_manifest(cases, manifest)
        selected = select_cases(cases, args)
        metadata = {
            "mode": "live" if args.live else "replay" if args.replay else "preview",
            "dataset_version": manifest["dataset_version"], "dataset_status": manifest.get("status"),
            "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
            "local_source_sha256": local_source_hash(), "label": args.label,
            "model_label_user_supplied": args.model_label, "base_url": args.base_url,
            "request_adaptation": "expected_walking_limit_to_request" if args.apply_expected_walking_limit else "none",
            "selected_case_ids": [case.case_id for case in selected], "repeats": args.repeats,
            "planned_runs": len(selected) * args.repeats, "timeout_s": args.timeout,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "replay_input_sha256": hashlib.sha256(args.replay.read_bytes()).hexdigest() if args.replay else None,
            "versions": {name: importlib.metadata.version(name) for name in ("httpx", "pydantic")},
        }
        if not args.live and args.replay is None:
            print(json.dumps(metadata, ensure_ascii=False, indent=2))
            print("Preview only. No requests or result files were created. Use --live or --replay to execute.")
            return 0
        inputs = load_replay(args.replay) if args.replay else None
        out_dir = asyncio.run(execute(args, selected, metadata, inputs))
    except (OSError, ValueError, KeyError, httpx.InvalidURL) as error:
        print(f"Evaluation setup/output failed: {error}", file=sys.stderr)
        return 1
    print(f"Saved evaluation files: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
