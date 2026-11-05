"""餐饮不由整趟路线阻塞，失败按时段说明，景点变化时撤去旧候选。"""

import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from mcp.types import CallToolResult

from app.agents.trip_planner_agent import TripPlannerAgent
from app.models.schemas import DiningResolution
from app.services.amap_service import AmapService, AmapServiceError
from app.services.dining_service import DiningService, DiningState
import test_dining_service as dining_tests
from test_walking_limits import place, plan, request


class IndependentDiningTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_missing_route_does_not_clear_any_days_meals(self):
        maps = dining_tests.DiningTests().maps()
        req, current = dining_tests.inputs()
        second = current.days[0].model_copy(deep=True)
        second.day_index, second.date = 1, "2026-11-06"
        current.days.append(second)
        current.days[0].routes = []
        before = req.model_dump()
        await DiningService(maps).finalize(current, req, DiningState())
        self.assertEqual([len(d.meals) for d in current.days], [2, 2])
        self.assertTrue(current.days[0].dining_resolution.based_on_provisional_itinerary)
        self.assertFalse(current.days[1].dining_resolution.based_on_provisional_itinerary)
        self.assertEqual(current.days[0].routes, [])
        self.assertEqual(req.model_dump(), before)
        self.assertTrue(all("暂定景点" in m.description for m in current.days[0].meals))

    async def test_first_day_provider_failure_does_not_stop_second_day(self):
        maps = AmapService()
        req, current = dining_tests.inputs()
        _, other = dining_tests.inputs("CD")
        second = other.days[0]
        second.day_index, second.date = 1, "2026-11-06"
        for a in second.attractions:
            a.location.longitude += .1
        current.days.append(second)
        async def call(name, args):
            lon = float(args["location"].split(",")[0])
            if lon < 116.5:
                raise AmapServiceError("private")
            return CallToolResult(content=[], structuredContent={"pois": [{"id": "meal-"+str(lon), "name": "候选餐馆", "address": "地址", "location": args["location"]}]})
        maps._call_tool = AsyncMock(side_effect=call)
        await DiningService(maps).finalize(current, req, DiningState())
        self.assertEqual([len(d.meals) for d in current.days], [0, 2])
        self.assertEqual(current.days[0].dining_resolution.status, "needs_choice")
        self.assertEqual(current.days[1].dining_resolution.status, "candidates_available")
        self.assertNotIn("private", current.model_dump_json())

    async def test_partial_slots_preserve_success_and_explain_timeout(self):
        maps = dining_tests.DiningTests().maps()
        original = maps._call_tool.side_effect
        async def call(name, args):
            if float(args["location"].split(",")[0]) > 116.405:
                raise TimeoutError("private")
            return await original(name, args)
        maps._call_tool.side_effect = call
        req, current = dining_tests.inputs()
        await DiningService(maps).finalize(current, req, DiningState())
        result = current.days[0].dining_resolution
        self.assertEqual((result.status, result.provided_slots), ("partial", 1))
        self.assertEqual(result.unresolved[0].meal_type, "dinner")
        self.assertEqual(result.unresolved[0].reason, "timeout")
        self.assertTrue(all(x.requires_confirmation and x.verification == "not_verified" for x in result.alternatives))

    async def test_changed_anchor_with_failed_search_removes_old_meals(self):
        maps = dining_tests.DiningTests().maps()
        req, first = dining_tests.inputs()
        state = DiningState()
        await DiningService(maps).finalize(first, req, state)
        req2, changed = dining_tests.inputs("CD")
        changed.days[0].meals = first.days[0].meals
        maps._call_tool.side_effect = AmapServiceError("unavailable")
        await DiningService(maps).finalize(changed, req2, state)
        self.assertEqual(changed.days[0].meals, [])
        self.assertEqual(changed.days[0].dining_resolution.status, "needs_choice")
        self.assertEqual([x.anchor_poi_id for x in changed.days[0].dining_resolution.unresolved], ["C", "D"])

    async def test_strict_preferences_stay_unchanged_and_are_not_offered_as_relaxation(self):
        maps = dining_tests.DiningTests().maps([])
        req, current = dining_tests.inputs()
        req.dining_preference = "清真"
        before = req.model_dump()
        await DiningService(maps).finalize(current, req, DiningState())
        result = current.days[0].dining_resolution
        self.assertEqual(req.model_dump(), before)
        self.assertEqual(result.preference_verification, "not_verified")
        self.assertNotIn("adjust_preferences", [x.kind for x in result.alternatives])
        self.assertEqual(maps._call_tool.await_count, 2)

    async def test_coordinate_lookup_failure_is_explained_without_blocking_other_slot(self):
        maps = dining_tests.DiningTests().maps()
        req, current = dining_tests.inputs()
        current.days[0].attractions[0].location = None
        maps.get_poi_detail = AsyncMock(side_effect=AmapServiceError("secret"))
        await DiningService(maps).finalize(current, req, DiningState())
        report = current.days[0].dining_resolution
        self.assertEqual(report.provided_slots, 1)
        self.assertEqual(report.unresolved[0].reason, "provider_error")
        self.assertNotIn("secret", current.model_dump_json())

    async def test_expired_budget_returns_choices_without_any_network_call(self):
        maps = dining_tests.DiningTests().maps()
        req, current = dining_tests.inputs()
        with patch("app.services.dining_service.DINING_BUDGET_S", 0):
            await DiningService(maps).finalize(current, req, DiningState())
        maps._call_tool.assert_not_awaited()
        self.assertEqual([x.reason for x in current.days[0].dining_resolution.unresolved], ["budget_exhausted"]*2)

    async def test_llm_cannot_forge_day_dining_status_and_schema_excludes_it(self):
        payload = plan().model_dump()
        payload["days"][0]["dining_resolution"] = {"malformed": "verified"}
        planner = TripPlannerAgent(llm=Mock(), amap_service=Mock())
        with patch("app.agents.trip_planner_agent.SimpleAgent") as model:
            model.return_value.run.return_value = json.dumps(payload)
            result = planner._generate_plan(request(), [place("A"), place("B")], [])
        self.assertIsNone(result.days[0].dining_resolution)
        self.assertNotIn("DiningResolution", json.dumps(planner._model_schema()))

    async def test_null_extra_text_and_empty_day_are_supported(self):
        maps = dining_tests.DiningTests().maps()
        req, current = dining_tests.inputs()
        req.free_text_input = None
        self.assertTrue(DiningService._can_relax(req))
        current.days[0].attractions = []
        await DiningService(maps).finalize(current, req, DiningState())
        self.assertEqual(current.days[0].dining_resolution.status, "not_applicable")
        maps._call_tool.assert_not_awaited()

    async def test_report_schema_rejects_false_slot_count(self):
        maps = dining_tests.DiningTests().maps()
        req, current = dining_tests.inputs()
        await DiningService(maps).finalize(current, req, DiningState())
        data = current.days[0].dining_resolution.model_dump()
        data["provided_slots"] = 0
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            DiningResolution.model_validate(data)
