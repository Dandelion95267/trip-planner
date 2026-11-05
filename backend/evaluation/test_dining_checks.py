"""独立餐饮说明检查：不误认菜单已核实，不接受旧景点餐馆或伪造距离。"""

import sys
from pathlib import Path
import unittest

from app.services.dining_service import DiningService, DiningState
from evaluation.dining_checks import evaluate_dining_resolution

# 复用带真实解析器的离线地图夹具，不访问外部API。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
import test_dining_service as fixtures


class DiningChecksTests(unittest.IsolatedAsyncioTestCase):
    async def inputs(self, missing=False):
        req, plan = fixtures.inputs()
        plan.days[0].routes = []
        maps = fixtures.DiningTests().maps([] if missing else None)
        await DiningService(maps).finalize(plan, req, DiningState())
        return req, plan

    async def test_provisional_candidates_pass_without_passing_original_routes(self):
        req, plan = await self.inputs()
        self.assertEqual(evaluate_dining_resolution(req, plan)["status"], "passed")
        self.assertEqual(plan.days[0].routes, [])

    async def test_empty_candidates_with_reasons_and_choices_pass_delivery_check(self):
        req, plan = await self.inputs(missing=True)
        self.assertEqual(evaluate_dining_resolution(req, plan)["status"], "passed")
        self.assertEqual(plan.days[0].dining_resolution.status, "needs_choice")

    async def test_stale_anchor_and_fabricated_distance_are_rejected(self):
        req, plan = await self.inputs()
        plan.days[0].meals[0].anchor_poi_id = "old"
        self.assertEqual(evaluate_dining_resolution(req, plan)["status"], "failed")
        req, plan = await self.inputs()
        plan.days[0].meals[0].straight_line_distance_m = 0
        self.assertEqual(evaluate_dining_resolution(req, plan)["status"], "failed")

    async def test_changed_preference_or_false_route_status_is_rejected(self):
        req, plan = await self.inputs()
        plan.days[0].dining_resolution.requested_preference = "anything"
        self.assertEqual(evaluate_dining_resolution(req, plan)["status"], "failed")
        req, plan = await self.inputs()
        plan.days[0].dining_resolution.based_on_provisional_itinerary = False
        self.assertEqual(evaluate_dining_resolution(req, plan)["status"], "failed")

    async def test_legacy_report_is_not_backfilled_as_a_pass(self):
        req, plan = fixtures.inputs()
        self.assertEqual(evaluate_dining_resolution(req, plan)["status"], "not_evaluated")
