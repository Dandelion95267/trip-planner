"""新偏好证据不能靠店名、查询词或伪造字段冒充实际菜单确认。"""

import sys
from pathlib import Path
import unittest

from app.services.dining_service import DiningService, DiningState
from evaluation.dining_preference_checks import evaluate_dining_preferences

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
import test_dining_preferences as fixtures
import test_dining_service as base


class PreferenceEvidenceChecks(unittest.IsolatedAsyncioTestCase):
    async def inputs(self):
        item = {"id": "r", "name": "川菜馆", "type": "四川菜(川菜)", "location": "116.405,39.9"}
        maps = fixtures.maps_with([item])
        req, plan = base.inputs()
        req.city = plan.city = "成都"
        req.dining_preference = "本地菜 清淡"
        await DiningService(maps).finalize(plan, req, DiningState())
        plan.candidate_evidence = maps.source_evidence.snapshot()
        return req, plan

    async def test_real_map_category_plus_unknown_taste_passes_contract(self):
        req, plan = await self.inputs()
        self.assertEqual(evaluate_dining_preferences(req, plan)["status"], "passed")

    async def test_taste_cannot_be_marked_supported_from_map_category(self):
        req, plan = await self.inputs()
        plan.days[0].meals[0].preference_evidence[1].status = "supported"
        self.assertEqual(evaluate_dining_preferences(req, plan)["status"], "failed")

    async def test_fabricated_category_quote_is_rejected(self):
        req, plan = await self.inputs()
        plan.days[0].meals[0].preference_evidence[0].value = "invented 四川菜"
        self.assertEqual(evaluate_dining_preferences(req, plan)["status"], "failed")

    async def test_name_cannot_be_promoted_to_category_evidence(self):
        req, plan = await self.inputs()
        ev = plan.days[0].meals[0].preference_evidence[0]
        ev.source, ev.value = "poi_name", "川菜馆"
        self.assertEqual(evaluate_dining_preferences(req, plan)["status"], "failed")

    async def test_omitting_unverified_taste_is_rejected(self):
        req, plan = await self.inputs()
        plan.days[0].meals[0].preference_evidence.pop()
        self.assertEqual(evaluate_dining_preferences(req, plan)["status"], "failed")

    async def test_missing_same_run_evidence_is_unknown(self):
        req, plan = await self.inputs()
        plan.candidate_evidence = None
        self.assertEqual(evaluate_dining_preferences(req, plan)["status"], "unknown")

    async def test_legacy_record_is_not_backfilled(self):
        req, plan = base.inputs()
        self.assertEqual(evaluate_dining_preferences(req, plan)["status"], "not_evaluated")
