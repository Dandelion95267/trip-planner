"""验证人数、间夜、未知金额与最终行程预算，不调用外部服务。"""

import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.main import app
from app.models.schemas import Budget, BudgetOptions, TripPlan, TripRequest, TripReviewResult
from app.services.budget_service import estimate_budget


def make_plan(counts=(2, 1)):
    return TripPlan(
        city="北京", start_date="2026-11-01", end_date=f"2026-11-{len(counts):02d}",
        days=[{
            "date": f"2026-11-{index + 1:02d}", "day_index": index,
            "description": "当天行程", "transportation": "公共交通", "accommodation": "酒店",
            "attractions": [{
                "poi_id": f"{index}-{place}", "name": f"景点{place}", "address": "地址",
                "visit_duration": 60, "description": "游览建议", "ticket_price": 999,
            } for place in range(count)],
        } for index, count in enumerate(counts)], overall_suggestions="出行前确认。",
    )


class BudgetCalculationTests(unittest.TestCase):
    def test_group_total_uses_room_nights_and_person_days(self):
        budget = estimate_budget(make_plan(), BudgetOptions(
            travelers=2, rooms=1, hotel_per_room_night=300,
            meals_per_person_day=100, transport_per_person_day=20, ticket_per_person_visit=50,
        ))
        self.assertEqual(budget.nights, 1)
        self.assertEqual(budget.total_hotels, 300)
        self.assertEqual(budget.total_meals, 400)
        self.assertEqual(budget.total_transportation, 80)
        self.assertEqual(budget.total_attractions, 300)
        self.assertEqual(budget.total, 1080)
        self.assertEqual(budget.known_subtotal, 1080)
        self.assertTrue(budget.is_complete)

    def test_one_day_has_no_lodging_even_without_hotel_rate(self):
        budget = estimate_budget(make_plan((1,)), BudgetOptions(
            meals_per_person_day=60, transport_per_person_day=30, ticket_per_person_visit=20,
        ))
        self.assertEqual(budget.nights, 0)
        self.assertEqual(budget.total_hotels, 0)
        self.assertEqual(budget.total, 110)
        self.assertTrue(budget.is_complete)

    def test_unknown_categories_do_not_become_zero_or_complete_total(self):
        budget = estimate_budget(make_plan(), BudgetOptions(hotel_per_room_night=300))
        self.assertEqual(budget.known_subtotal, 300)
        self.assertIsNone(budget.total)
        self.assertIsNone(budget.total_attractions)
        self.assertIsNone(budget.total_meals)
        self.assertIsNone(budget.total_transportation)
        self.assertFalse(budget.is_complete)

    def test_explicit_zero_is_distinct_from_missing_rate(self):
        budget = estimate_budget(make_plan(), BudgetOptions(
            hotel_per_room_night=0, meals_per_person_day=0,
            transport_per_person_day=0, ticket_per_person_visit=0,
        ))
        self.assertEqual(budget.total, 0)
        self.assertTrue(budget.is_complete)
        self.assertTrue(all(item.subtotal == 0 and item.unit_price == 0 for item in budget.items))

    def test_several_rooms_are_not_multiplied_by_travelers_again(self):
        budget = estimate_budget(make_plan((1, 1, 1)), BudgetOptions(
            travelers=4, rooms=2, hotel_per_room_night=300,
        ))
        self.assertEqual(budget.nights, 2)
        self.assertEqual(budget.total_hotels, 1200)

    def test_estimate_does_not_use_or_modify_place_prices(self):
        plan = make_plan()
        before = plan.model_dump()
        budget = estimate_budget(plan, BudgetOptions(ticket_per_person_visit=10))
        self.assertEqual(budget.total_attractions, 30)
        self.assertEqual(plan.model_dump(), before)

    def test_repeated_visits_are_counted_as_visits_not_unique_pois(self):
        plan = make_plan((1, 1))
        plan.days[1].attractions[0].poi_id = plan.days[0].attractions[0].poi_id
        budget = estimate_budget(plan, BudgetOptions(ticket_per_person_visit=10))
        self.assertEqual(budget.total_attractions, 20)

    def test_deleting_attraction_changes_only_related_cost(self):
        plan = make_plan()
        options = BudgetOptions(hotel_per_room_night=300, ticket_per_person_visit=50)
        before = estimate_budget(plan, options)
        plan.days[0].attractions.pop()
        after = estimate_budget(plan, options)
        self.assertEqual(before.total_attractions - after.total_attractions, 50)
        self.assertEqual(before.total_hotels, after.total_hotels)

    def test_rejects_negative_fractional_or_excessive_inputs(self):
        for values in [{"travelers": 0}, {"rooms": 51}, {"hotel_per_room_night": -1},
                       {"meals_per_person_day": 2.5}, {"transport_per_person_day": 1000001}]:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                BudgetOptions(**values)


class BudgetEndpointTests(unittest.TestCase):
    def setUp(self):
        self.request = TripRequest(
            city="北京", start_date="2026-11-01", end_date="2026-11-02", travel_days=2,
            transportation="公共交通", accommodation="酒店", enable_review=True,
        ).model_dump()

    def call_endpoint(self, plan):
        workflow = Mock()
        workflow.plan_trip = AsyncMock(return_value=plan)
        with TestClient(app) as client, patch("app.api.main.TripPlannerAgent"), \
             patch("app.api.main.TripPlanningWorkflow", return_value=workflow):
            return client.post("/api/trip/plan", json=self.request)

    def test_budget_uses_final_workflow_output_and_replaces_untrusted_sum(self):
        self.request["budget_options"] = {"travelers": 2, "ticket_per_person_visit": 50}
        final = make_plan((1, 1))
        final.budget = Budget(total=999999)
        response = self.call_endpoint(final)
        self.assertEqual(response.status_code, 200)
        budget = response.json()["budget"]
        self.assertEqual(budget["total_attractions"], 200)
        self.assertEqual(budget["known_subtotal"], 200)
        self.assertIsNone(budget["total"])
        self.assertEqual(budget["source"], "user_estimate")

    def test_disabled_estimate_clears_any_model_budget(self):
        final = make_plan()
        final.budget = Budget(total=999999)
        response = self.call_endpoint(final)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["budget"])

    def test_invalid_amount_is_rejected_before_planning(self):
        self.request["budget_options"] = {"hotel_per_room_night": -10}
        with TestClient(app) as client, patch("app.api.main.TripPlanningWorkflow") as workflow:
            self.assertEqual(client.post("/api/trip/plan", json=self.request).status_code, 422)
        workflow.assert_not_called()

    def test_budget_runs_after_revision_and_keeps_final_review(self):
        self.request["budget_options"] = {"travelers": 2, "ticket_per_person_visit": 50}
        planner = Mock()
        planner.prepare_context = AsyncMock(return_value=object())
        planner.generate_plan = AsyncMock(side_effect=[make_plan(), make_plan((1, 1))])
        reviewer = Mock()
        reviewer.review_trip = AsyncMock(side_effect=[
            TripReviewResult(passed=False, summary="减少第一天景点。", issues=[{
                "category": "pace", "day_index": 0, "problem": "游览强度较大。",
                "evidence": "第一天有两个游览景点。", "suggestion": "减少一个景点。",
            }]),
            TripReviewResult(passed=True, summary="未发现待修改问题。"),
        ])
        with TestClient(app) as client, patch("app.api.main.TripPlannerAgent", return_value=planner), \
             patch("app.services.trip_workflow.TripReviewAgent", return_value=reviewer):
            response = client.post("/api/trip/plan", json=self.request)
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["review"]["status"], "passed")
        self.assertEqual(result["review"]["revisions"], 1)
        self.assertEqual(result["budget"]["total_attractions"], 200)
        self.assertEqual(len(result["days"][0]["attractions"]), 1)


if __name__ == "__main__":
    unittest.main()
