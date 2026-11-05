"""来源采集与完整规划串联测试；仅模拟工具和模型，不联网。"""

import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from mcp.types import CallToolResult

from app.agents.trip_planner_agent import TripPlannerAgent
from app.models.schemas import POIInfo, TripRequest
from app.services.amap_service import AmapService, AmapServiceError
from app.services.source_evidence import SourceEvidenceRecorder
from evaluation.evaluators import check_sources


def tool_result(value):
    return CallToolResult(content=[], structuredContent=value)


class SourceEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_and_detail_are_copied_at_tool_boundary(self):
        maps = AmapService()
        with patch.object(maps, "_call_tool", new_callable=AsyncMock, side_effect=[
            tool_result({"pois": [{"id": "A", "name": "原名称", "address": "原地址"}]}),
            tool_result({"id": "A", "name": "原名称", "address": "原地址", "location": "116.4,39.9"}),
        ]):
            places = await maps.search_poi("景点", "北京")
            maps.source_evidence.register("candidates", places)
            places[0].name = "修改后的名称"
            detail = await maps.get_poi_detail("A")
            detail.location.longitude = 0
        snapshot = maps.source_evidence.snapshot()
        self.assertEqual(snapshot.searches[0].pois[0].name, "原名称")
        self.assertEqual(snapshot.candidates[0].name, "原名称")
        self.assertEqual(snapshot.details[0].poi.location.longitude, 116.4)
        snapshot.candidates[0].name = "再次修改"
        self.assertEqual(maps.source_evidence.snapshot().candidates[0].name, "原名称")

    async def test_failed_tool_does_not_create_a_successful_search_snapshot(self):
        maps = AmapService()
        with patch.object(maps, "_call_tool", new_callable=AsyncMock, return_value=tool_result({"bad": []})):
            with self.assertRaises(AmapServiceError):
                await maps.search_poi("景点", "北京")
        self.assertEqual(maps.source_evidence.snapshot().searches, [])

    def test_request_ids_are_isolated_and_parameters_are_allowlisted(self):
        a, b = SourceEvidenceRecorder(), SourceEvidenceRecorder()
        self.assertNotEqual(a.request_id, b.request_id)
        a.search("maps_text_search", {"keywords": "餐馆", "api_key": "private", "url": "private"}, [])
        self.assertNotIn("private", a.snapshot().model_dump_json())
        self.assertEqual(b.searches, [])

    async def test_real_service_pipeline_exports_sources_and_rebuilds_final_dining(self):
        maps = AmapService()
        locations = {"A": "116.4,39.9", "B": "116.41,39.91"}
        async def call(name, arguments):
            if name == "maps_text_search":
                return tool_result({"pois": [{"id": k, "name": f"景点{k}", "address": f"地址{k}"} for k in "AB"] if arguments["keywords"] == "旅游景点" else []})
            if name == "maps_weather":
                return tool_result({"forecasts": []})
            if name == "maps_search_detail":
                k = arguments["id"]
                return tool_result({"id": k, "name": f"景点{k}", "address": f"地址{k}", "location": locations[k]})
            if name == "maps_direction_transit_integrated":
                return tool_result({"transits": [{"duration": "600", "walking_distance": "500"}]})
            if name == "maps_around_search":
                k = "L" if arguments["location"].startswith("116.4,") else "D"
                return tool_result({"pois": [{"id": k, "name": f"餐馆{k}", "address": f"餐馆地址{k}", "location": arguments["location"]}]})
            self.fail(name)
        request = TripRequest(city="北京", start_date="2026-11-05", end_date="2026-11-05", travel_days=1,
                              transportation="公共交通", route_mode="transit", max_leg_walking_m=1000,
                              accommodation="酒店")
        response = {"city": request.city, "start_date": request.start_date, "end_date": request.end_date,
                    "days": [{"date": request.start_date, "day_index": 0, "description": "旧餐馆牡丹园的过期描述",
                              "transportation": "公共交通", "accommodation": "酒店",
                              "attractions": [{"poi_id": k, "name": "模型名称", "address": "模型地址", "visit_duration": 90, "description": "参观"} for k in "AB"]}],
                    "overall_suggestions": "旧餐馆的过期建议", "request_id": "model-forged", "candidate_evidence": {"fake": "private"}}
        with patch.object(maps, "_call_tool", new_callable=AsyncMock, side_effect=call), patch("app.agents.trip_planner_agent.SimpleAgent") as model:
            model.return_value.run.return_value = json.dumps(response)
            planner = TripPlannerAgent(llm=Mock(), amap_service=maps)
            plan = await planner.plan_trip(request)
            prompt = json.loads(model.return_value.run.call_args.args[0])
        self.assertNotIn("CandidateEvidence", json.dumps(prompt["输出JSON结构"]))
        self.assertNotEqual(plan.request_id, "model-forged")
        self.assertEqual(plan.request_id, plan.candidate_evidence.request_id)
        self.assertEqual(check_sources(plan, plan.candidate_evidence.model_dump())["status"], "passed")
        self.assertEqual([m.poi_id for m in plan.days[0].meals], ["L", "D"])
        self.assertNotIn("过期", plan.days[0].description + plan.overall_suggestions)
        self.assertEqual({t.stage for t in plan.stage_timings}, {"context", "model", "routes", "dining"})
        self.assertNotIn("private", plan.model_dump_json())


if __name__ == "__main__":
    unittest.main()
