"""Validate evaluation inputs and metadata locally; never call model/map services."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.schemas import TripRequest


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    min_daily_attractions: int = Field(default=1, ge=1, le=20)
    max_daily_attractions: int | None = Field(default=None, ge=1, le=20)
    allowed_route_modes: list[Literal["walking", "transit", "driving"]] | None = Field(
        default=None, min_length=1
    )
    require_complete_adjacent_routes: bool = False
    max_leg_walking_m: int | None = Field(default=None, ge=0, le=50000)
    require_matching_dates: bool = True
    require_candidate_poi_ids: bool = True
    require_budget_consistency: bool = True

    @model_validator(mode="after")
    def check_constraints(self) -> Expected:
        if self.max_daily_attractions is not None and self.max_daily_attractions < self.min_daily_attractions:
            raise ValueError("daily maximum must not be below daily minimum")
        if self.allowed_route_modes is not None and len(set(self.allowed_route_modes)) != len(self.allowed_route_modes):
            raise ValueError("allowed route modes must be unique")
        if self.max_leg_walking_m is not None:
            if not self.require_complete_adjacent_routes or self.min_daily_attractions < 2:
                raise ValueError("walking limits require complete routes and at least two attractions per day")
            if not self.allowed_route_modes or "driving" in self.allowed_route_modes:
                raise ValueError("walking-limit cases must specify walking/transit modes with measurable walking")
        return self


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    dataset_version: str = Field(min_length=1)
    case_id: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    split: Literal["development", "holdout"]
    category: Literal["basic", "count", "transport", "preference"]
    pilot: bool = False
    origin: Literal["synthetic", "adapted_acceptance"]
    tags: list[str] = Field(min_length=1)
    request: TripRequest
    expected: Expected
    manual_checks: list[str] = Field(min_length=1)

    @field_validator("request", mode="before")
    @classmethod
    def reject_unknown_request_fields(cls, value):
        if isinstance(value, dict):
            if set(value) - set(TripRequest.model_fields):
                raise ValueError("request contains fields absent from the current TripRequest schema")
        return value

    @field_validator("tags", "manual_checks")
    @classmethod
    def reject_blank_items(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("list items must not be blank")
        return values

    @model_validator(mode="after")
    def check_case(self) -> EvaluationCase:
        if not self.request.city.strip():
            raise ValueError("evaluation requests must name a nonblank city")
        if self.pilot and self.split != "development":
            raise ValueError("pilot cases must belong to the development split")
        if self.category == "count" and self.expected.max_daily_attractions is None:
            raise ValueError("count cases need an explicit daily maximum")
        if self.category == "transport":
            if not self.expected.require_complete_adjacent_routes or self.expected.min_daily_attractions < 2:
                raise ValueError("transport cases need complete routes and at least two attractions per day")
        mode = self.request.route_mode
        if mode and self.expected.allowed_route_modes:
            possible = {"walking", "transit"} if mode == "auto" else {mode}
            if not set(self.expected.allowed_route_modes) <= possible:
                raise ValueError("expected modes contradict the requested route mode")
        return self


def load_cases(path: Path) -> list[EvaluationCase]:
    cases = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            cases.append(EvaluationCase.model_validate(value))
        except json.JSONDecodeError as error:
            raise ValueError(f"{path.name}:{line_number}: invalid JSON") from error
        except ValidationError as error:
            # Include locations/messages, not full request values, in validation errors.
            messages = "; ".join(
                f"{'.'.join(str(part) for part in item['loc']) or 'case'}: {item['msg']}"
                for item in error.errors(include_input=False, include_url=False)
            )
            raise ValueError(f"{path.name}:{line_number}: {messages}") from error
    if not cases:
        raise ValueError("dataset must contain at least one case")
    return cases


def validate_manifest(cases: list[EvaluationCase], manifest: dict) -> dict:
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("case IDs must be unique")
    fingerprints = [json.dumps(case.request.model_dump(), sort_keys=True, ensure_ascii=False) for case in cases]
    if len(fingerprints) != len(set(fingerprints)):
        raise ValueError("identical requests must not be duplicated across dataset cases")
    versions = {case.dataset_version for case in cases}
    if versions != {manifest.get("dataset_version")}:
        raise ValueError("case versions must match the manifest")
    summary = {
        "dataset_version": manifest["dataset_version"],
        "case_count": len(cases),
        "splits": dict(Counter(case.split for case in cases)),
        "categories": dict(Counter(case.category for case in cases)),
        "pilot_case_ids": [case.case_id for case in cases if case.pilot],
    }
    for key in ("case_count", "splits", "categories", "pilot_case_ids"):
        if summary[key] != manifest.get(key):
            raise ValueError(f"manifest {key} does not match dataset contents")
    for case in cases:
        if case.request.start_date != manifest.get("start_date"):
            raise ValueError(f"{case.case_id}: start date differs from the fixed batch anchor")
        for name in ("enable_review", "optimize_order"):
            expected_value = manifest.get("request_profile", {}).get(name)
            if not isinstance(expected_value, bool) or getattr(case.request, name) != expected_value:
                raise ValueError(f"{case.case_id}: {name} does not match the manifest profile")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("cases.jsonl"))
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("manifest.json"))
    args = parser.parse_args()
    try:
        cases = load_cases(args.cases)
        manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be a JSON object")
        summary = validate_manifest(cases, manifest)
        summary["cases_sha256"] = hashlib.sha256(args.cases.read_bytes()).hexdigest()
    except (OSError, ValueError, KeyError) as error:
        print(f"Dataset validation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("Dataset inputs and metadata are valid. No model/map calls were made; plan quality was not evaluated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
