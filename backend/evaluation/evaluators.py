"""Independent deterministic checks; never call models or production budget logic."""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from math import isfinite
from statistics import mean, median

from pydantic import ValidationError

from app.models.schemas import POIInfo, TripPlan
from evaluation.validate_cases import EvaluationCase
from evaluation.route_resolution_checks import evaluate_route_resolution
from evaluation.dining_checks import evaluate_dining_resolution
from evaluation.dining_preference_checks import evaluate_dining_preferences


def check(status: str, *details: str) -> dict:
    return {"status": status, "details": list(details)}


def verdict(failures: list[str], unknowns: list[str] | None = None) -> dict:
    if failures:
        return check("failed", *failures, *(unknowns or []))
    if unknowns:
        return check("unknown", *unknowns)
    return check("passed")


def check_sources(plan: TripPlan, evidence: dict | None) -> dict:
    if not isinstance(evidence, dict):
        return check("unknown", "Same-run candidate evidence was not captured.")
    if "version" in evidence:
        return check_captured_sources(plan, evidence)
    failures, unknowns = [], []
    groups = {
        "candidates": [item for day in plan.days for item in day.attractions],
        "hotel_candidates": [day.hotel for day in plan.days if day.hotel is not None],
        "restaurant_candidates": [item for day in plan.days for item in day.meals],
    }
    for name, places in groups.items():
        if not places:
            continue
        raw = evidence.get(name)
        if not isinstance(raw, list):
            unknowns.append(f"Missing {name} evidence.")
            continue
        try:
            candidates = [POIInfo.model_validate(item) for item in raw]
        except (ValidationError, TypeError):
            unknowns.append(f"Invalid {name} evidence.")
            continue
        ids = [item.id for item in candidates]
        if len(ids) != len(set(ids)):
            unknowns.append(f"Ambiguous duplicate IDs in {name} evidence.")
            continue
        by_id = {item.id: item for item in candidates}
        for place in places:
            original = by_id.get(place.poi_id)
            if original is None:
                failures.append(f"POI {place.poi_id!r} is absent from {name}.")
            elif place.name != original.name or (place.address or "") != original.address:
                failures.append(f"POI {place.poi_id!r} name/address disagrees with evidence.")
            elif original.location is not None and place.location != original.location:
                failures.append(f"POI {place.poi_id!r} coordinates disagree with evidence.")
            elif original.location is None and place.location is not None:
                # Route/detail lookup may have legitimately supplemented these coordinates.
                unknowns.append(f"POI {place.poi_id!r} supplemented coordinates need detail evidence.")
    return verdict(failures, unknowns)


def check_captured_sources(plan: TripPlan, evidence: dict) -> dict:
    """新增采集链路的核验；旧记录仍走原规则，不事后补造来源。"""
    from datetime import datetime
    from app.models.schemas import CandidateEvidence
    try:
        captured = CandidateEvidence.model_validate(evidence)
        for entry in captured.searches + captured.details:
            stamp = datetime.fromisoformat(entry.collected_at_utc)
            if stamp.utcoffset() is None:
                raise ValueError("missing timezone")
    except (ValidationError, ValueError, TypeError):
        return check("unknown", "Invalid captured source evidence or timestamp.")
    if not plan.request_id or captured.request_id != plan.request_id:
        return check("failed", "Source evidence request ID does not match the delivered plan.")
    if not captured.searches:
        return check("unknown", "No same-run search snapshots were captured.")
    failures, unknowns = [], []
    observations = [poi for search in captured.searches for poi in search.pois]
    details = [entry.poi for entry in captured.details]
    groups = {
        "candidates": [x for day in plan.days for x in day.attractions],
        "hotel_candidates": [day.hotel for day in plan.days if day.hotel is not None],
        "restaurant_candidates": [x for day in plan.days for x in day.meals],
    }
    for group, places in groups.items():
        candidates = getattr(captured, group)
        if len({p.id for p in candidates}) != len(candidates):
            unknowns.append(f"Ambiguous duplicate IDs in {group} evidence.")
            continue
        by_id = {p.id: p for p in candidates}
        for place in places:
            original = by_id.get(place.poi_id)
            if original is None:
                failures.append(f"POI {place.poi_id!r} is absent from {group}.")
                continue
            matching = [p for p in observations if p.id == original.id
                        and p.name == original.name and p.address == original.address]
            if not matching:
                failures.append(f"POI {place.poi_id!r} is not backed by a captured search result.")
            if place.name != original.name or (place.address or "") != original.address:
                failures.append(f"POI {place.poi_id!r} name/address disagrees with evidence.")
            if place.location is not None:
                coordinates = [p.location for p in observations + details if p.id == place.poi_id and p.location is not None]
                if not coordinates:
                    unknowns.append(f"POI {place.poi_id!r} supplemented coordinates need detail evidence.")
                elif place.location not in coordinates:
                    failures.append(f"POI {place.poi_id!r} coordinates disagree with captured evidence.")
            elif original.location is not None:
                failures.append(f"POI {place.poi_id!r} lost its known coordinates.")
    return verdict(failures, unknowns)


def check_budget(case: EvaluationCase, plan: TripPlan) -> dict:
    options, budget = case.request.budget_options, plan.budget
    if options is None:
        return check("passed") if budget is None else check("failed", "Budget exists although estimation was disabled.")
    if budget is None:
        return check("failed", "Requested budget is missing.")
    days = len(plan.days)
    nights = max(days - 1, 0)
    visits = sum(len(day.attractions) for day in plan.days)
    standards = {
        "attractions": (visits * options.travelers, options.ticket_per_person_visit),
        "hotels": (nights * options.rooms, options.hotel_per_room_night),
        "meals": (days * options.travelers, options.meals_per_person_day),
        "transportation": (days * options.travelers, options.transport_per_person_day),
    }
    amounts = {
        name: 0 if quantity == 0 else None if rate is None else quantity * rate
        for name, (quantity, rate) in standards.items()
    }
    subtotal = sum(value for value in amounts.values() if value is not None)
    complete = all(value is not None for value in amounts.values())
    expected = {
        "travelers": options.travelers, "rooms": options.rooms, "nights": nights,
        "known_subtotal": subtotal, "is_complete": complete,
        "total": subtotal if complete else None,
        **{f"total_{name}": value for name, value in amounts.items()},
    }
    failures = [f"Budget {name} differs from independent calculation." for name, value in expected.items() if getattr(budget, name) != value]
    categories = [item.category for item in budget.items]
    if len(categories) != 4 or set(categories) != set(standards):
        failures.append("Budget must contain exactly the four expected category items.")
    else:
        for item in budget.items:
            quantity, rate = standards[item.category]
            if (item.quantity, item.unit_price, item.subtotal) != (quantity, rate, amounts[item.category]):
                failures.append(f"Budget item {item.category} has incorrect quantity/rate/subtotal.")
    return verdict(failures)


def evaluate_case(case: EvaluationCase, result, candidate_evidence: dict | None = None) -> dict:
    checks = {}
    try:
        plan = TripPlan.model_validate(result)
        if not plan.days:
            raise ValueError("An empty plan is not usable.")
    except (ValidationError, TypeError, ValueError):
        return {
            "delivered": False, "hard_status": "failed", "available_checks_pass": False,
            "checks": {"structure": check("failed", "No usable TripPlan was delivered.")},
            "routes": {"expected": 0, "matched": 0},
            "manual_review": "not_delivered",
        }
    checks["structure"] = check("passed")
    expected = case.expected
    start = date.fromisoformat(case.request.start_date)
    dates = [(start + timedelta(days=index)).isoformat() for index in range(case.request.travel_days)]
    dates_ok = (
        (plan.city, plan.start_date, plan.end_date) == (case.request.city, case.request.start_date, case.request.end_date)
        and [day.date for day in plan.days] == dates
        and [day.day_index for day in plan.days] == list(range(case.request.travel_days))
        and all(item.date in dates for item in plan.weather_info)
    )
    checks["dates"] = (check("passed") if dates_ok else check("failed", "City/dates/day indices or weather dates disagree with the request.")) if expected.require_matching_dates else check("not_applicable")
    counts_ok = len(plan.days) == case.request.travel_days and all(
        len(day.attractions) >= expected.min_daily_attractions
        and (expected.max_daily_attractions is None or len(day.attractions) <= expected.max_daily_attractions)
        for day in plan.days
    )
    checks["daily_count"] = check("passed") if counts_ok else check("failed", "At least one day violates the requested count bounds or a day is missing.")
    ids_ok = all(
        all(item.poi_id for item in day.attractions)
        and len({item.poi_id for item in day.attractions}) == len(day.attractions)
        for day in plan.days
    )
    checks["daily_poi_ids"] = check("passed") if ids_ok else check("failed", "A day contains missing or repeated attraction IDs.")

    integrity, modes_failed, modes_unknown, walking_failed, walking_unknown = [], [], [], [], []
    expected_legs = matched_legs = 0
    for day in plan.days:
        pairs = [(a.poi_id, b.poi_id) for a, b in zip(day.attractions, day.attractions[1:])]
        expected_legs += len(pairs)
        route_map = {}
        for route in day.routes:
            pair = (route.origin.id, route.destination.id)
            if pair not in pairs or pair in route_map:
                integrity.append(f"Day {day.day_index} has a stale, unrelated or duplicate route.")
                continue
            route_map[pair] = route
        for pair in pairs:
            route = route_map.get(pair)
            if route is None:
                if expected.allowed_route_modes:
                    modes_unknown.append(f"Day {day.day_index} has a missing route; mode is unknown.")
                if expected.max_leg_walking_m is not None:
                    walking_unknown.append(f"Day {day.day_index} has a missing route; walking is unknown.")
                continue
            matched_legs += 1
            if expected.allowed_route_modes and route.mode not in expected.allowed_route_modes:
                modes_failed.append(f"Day {day.day_index} route mode {route.mode} is not allowed.")
            if expected.max_leg_walking_m is not None:
                distance = route.distance_m if route.mode == "walking" else route.walking_distance_m if route.mode == "transit" else None
                if distance is None:
                    walking_unknown.append(f"Day {day.day_index} walking distance is unavailable.")
                elif distance > expected.max_leg_walking_m:
                    walking_failed.append(f"Day {day.day_index} walking distance {distance} exceeds {expected.max_leg_walking_m} metres.")
    checks["route_integrity"] = verdict(integrity)
    checks["route_modes"] = verdict(modes_failed, modes_unknown) if expected.allowed_route_modes and expected_legs else check("not_applicable")
    checks["route_completeness"] = (
        check("passed") if expected_legs and matched_legs == expected_legs else check("failed", "Required adjacent routes are missing or no required pair was generated.")
    ) if expected.require_complete_adjacent_routes else check("not_applicable")
    checks["walking_limit"] = (
        verdict(walking_failed, walking_unknown) if expected_legs else check("unknown", "No adjacent route exists to assess walking limits.")
    ) if expected.max_leg_walking_m is not None else check("not_applicable")
    checks["candidate_sources"] = check_sources(plan, candidate_evidence) if expected.require_candidate_poi_ids else check("not_applicable")
    checks["budget"] = check_budget(case, plan) if expected.require_budget_consistency else check("not_applicable")
    states = {item["status"] for item in checks.values()}
    status = "failed" if "failed" in states else "unknown" if "unknown" in states else "passed"
    return {
        "delivered": True, "hard_status": status, "available_checks_pass": "failed" not in states,
        "checks": checks, "routes": {"expected": expected_legs, "matched": matched_legs},
        "manual_review": "pending", "reported_review_status": plan.review.status if plan.review else None,
        "route_resolution_handling": evaluate_route_resolution(case.request, plan),
        "dining_resolution_handling": evaluate_dining_resolution(case.request, plan),
        "dining_preference_handling": evaluate_dining_preferences(case.request, plan),
    }


def summarize(records: list[dict]) -> dict:
    count = len(records)
    delivered = sum(record["evaluation"]["delivered"] for record in records)
    verified = sum(record["evaluation"]["hard_status"] == "passed" for record in records)
    available = sum(record["evaluation"]["available_checks_pass"] for record in records)
    expected = sum(record["evaluation"]["routes"]["expected"] for record in records)
    matched = sum(record["evaluation"]["routes"]["matched"] for record in records)
    timings = [record["elapsed_s"] for record in records if isinstance(record.get("elapsed_s"), (int, float)) and not isinstance(record["elapsed_s"], bool) and isfinite(record["elapsed_s"]) and record["elapsed_s"] >= 0]
    names = sorted({name for record in records for name in record["evaluation"]["checks"]})
    checks = {
        name: dict(Counter(record["evaluation"]["checks"].get(name, {"status": "not_evaluated"})["status"] for record in records))
        for name in names
    }
    return {
        "runs": count, "delivered": delivered, "delivery_rate": delivered / count if count else None,
        "verified_hard_passes": verified, "verified_hard_pass_rate": verified / count if count else None,
        "available_checks_passes": available, "available_checks_pass_rate": available / count if count else None,
        "hard_status_counts": dict(Counter(record["evaluation"]["hard_status"] for record in records)),
        "route_resolution_handling": dict(Counter(record["evaluation"].get("route_resolution_handling", {"status": "not_evaluated"})["status"] for record in records)),
        "dining_resolution_handling": dict(Counter(record["evaluation"].get("dining_resolution_handling", {"status": "not_evaluated"})["status"] for record in records)),
        "dining_preference_handling": dict(Counter(record["evaluation"].get("dining_preference_handling", {"status": "not_evaluated"})["status"] for record in records)),
        "checks": checks,
        "route_coverage_on_delivered_plans": {"expected": expected, "matched": matched, "rate": matched / expected if expected else None},
        "elapsed_s": {"count": len(timings), "mean": mean(timings) if timings else None, "median": median(timings) if timings else None, "max": max(timings) if timings else None},
        "token_usage": "not_collected", "actual_cost": "not_collected", "actual_model_tool_call_counts": "not_collected",
        "manual_quality": "not_scored",
    }
