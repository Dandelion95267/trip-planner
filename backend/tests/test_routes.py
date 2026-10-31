"""地点详情与路线接口测试，不发送真实网络请求。"""

import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from mcp.types import CallToolResult
from pydantic import ValidationError

from app.api.main import app
from app.models.schemas import POIInfo, RouteRequest
from app.services.amap_service import (
    AmapConfigurationError, AmapRouteNotFoundError, AmapService, AmapServiceError,
)


def result(data):
    return CallToolResult(content=[], structuredContent=data)


def point(poi_id, location="116.4,39.9"):
    return {"id": poi_id, "name": poi_id, "address": "地图地址", "location": location}


def route_data(mode):
    if mode == "transit":
        return {"distance": "99999", "transits": [{
            "duration": "300", "walking_distance": "120", "segments": [{
                "walking": {"steps": [{"instruction": "步行到车站"}]},
                "bus": {"buslines": [{"name": "测试公交", "departure_stop": {"name": "甲站"}, "arrival_stop": {"name": "乙站"}}]},
            }],
        }]}
    data = {"paths": [{"distance": "1000", "duration": "600", "steps": [{"instruction": "沿道路前行"}]}]}
    return {"route": data} if mode == "walking" else data


def endpoints():
    return (
        POIInfo(id="origin", name="起点", location={"longitude": 116.4, "latitude": 39.9}),
        POIInfo(id="destination", name="终点", location={"longitude": 116.5, "latitude": 39.95}),
    )


class RouteParsingTests(unittest.TestCase):
    def test_walking_and_driving_formats(self):
        for mode in ["walking", "driving"]:
            plan = AmapService._parse_route(result(route_data(mode)), *endpoints(), mode)
            self.assertEqual(plan.distance_m, 1000)
            self.assertEqual(plan.duration_s, 600)
            self.assertEqual(plan.steps, ["沿道路前行"])

    def test_transit_does_not_use_top_level_straight_line_distance(self):
        plan = AmapService._parse_route(result(route_data("transit")), *endpoints(), "transit")
        self.assertIsNone(plan.distance_m)
        self.assertEqual(plan.walking_distance_m, 120)
        self.assertEqual(plan.duration_s, 300)
        self.assertIn("乘坐测试公交：甲站 → 乙站", plan.steps)

    def test_empty_route_and_invalid_metrics_are_rejected(self):
        with self.assertRaises(AmapRouteNotFoundError):
            AmapService._parse_route(result({"paths": []}), *endpoints(), "driving")
        for value in ["未知", "12.5", -1, True, None]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    AmapService._parse_metric(value)
        with self.assertRaises(ValueError):
            AmapService._parse_route(result({"paths": [{"duration": "30"}]}), *endpoints(), "driving")

    def test_request_validation(self):
        for changes in [
            {"origin_poi_id": " "}, {"destination_poi_id": "origin"},
            {"mode": "transit"}, {"mode": "flying"},
        ]:
            with self.subTest(changes=changes):
                values = {"origin_poi_id": "origin", "destination_poi_id": "destination"}
                values.update(changes)
                with self.assertRaises(ValidationError):
                    RouteRequest(**values)


class RouteServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_detail_cache_is_local_to_service_instance_and_returns_copies(self):
        service = AmapService()
        with patch.object(AmapService, "_call_tool", new_callable=AsyncMock, return_value=result(point("origin"))) as call:
            first = await service.get_poi_detail("origin")
            first.name = "changed by caller"
            second = await service.get_poi_detail("origin")
            call.assert_awaited_once()
            self.assertEqual(second.name, "origin")
            await AmapService().get_poi_detail("origin")
            self.assertEqual(call.await_count, 2)

    async def test_details_are_resolved_before_correct_route_tool_is_called(self):
        tool_names = {
            "walking": "maps_direction_walking", "driving": "maps_direction_driving",
            "transit": "maps_direction_transit_integrated",
        }
        for mode, tool_name in tool_names.items():
            with self.subTest(mode=mode):
                responses = [result(point("origin")), result(point("destination", "116.5,39.95")), result(route_data(mode))]
                with patch.object(AmapService, "_call_tool", new_callable=AsyncMock, side_effect=responses) as call:
                    plan = await AmapService().plan_route(RouteRequest(
                        origin_poi_id="origin", destination_poi_id="destination", mode=mode, city="北京",
                    ))
                self.assertEqual(plan.origin.id, "origin")
                self.assertEqual(call.await_args_list[0].args, ("maps_search_detail", {"id": "origin"}))
                expected = {"origin": "116.4,39.9", "destination": "116.5,39.95"}
                if mode == "transit":
                    expected.update(city="北京", cityd="北京")
                self.assertEqual(call.await_args_list[2].args, (tool_name, expected))

    async def test_missing_coordinates_stop_before_route_query(self):
        responses = [result(point("origin", "")), result(point("destination"))]
        with patch.object(AmapService, "_call_tool", new_callable=AsyncMock, side_effect=responses) as call:
            with self.assertRaises(AmapRouteNotFoundError):
                await AmapService().plan_route(RouteRequest(origin_poi_id="origin", destination_poi_id="destination"))
        self.assertEqual(call.await_count, 2)

    async def test_mismatched_detail_id_is_rejected(self):
        with patch.object(AmapService, "_call_tool", new_callable=AsyncMock, return_value=result(point("wrong-id"))):
            with self.assertRaises(AmapServiceError):
                await AmapService().get_poi_detail("requested-id")

    async def test_malformed_route_returns_safe_service_error(self):
        responses = [result(point("origin")), result(point("destination")), result({"private": "unexpected"})]
        with patch.object(AmapService, "_call_tool", new_callable=AsyncMock, side_effect=responses):
            with self.assertRaises(AmapServiceError) as caught:
                await AmapService().plan_route(RouteRequest(origin_poi_id="origin", destination_poi_id="destination"))
        self.assertNotIn("private", str(caught.exception))


class RouteEndpointTests(unittest.TestCase):
    def test_success_and_invalid_input(self):
        plan = AmapService._parse_route(result(route_data("walking")), *endpoints(), "walking")
        with TestClient(app) as client, patch("app.api.main.AmapService") as service:
            service.return_value.plan_route = AsyncMock(return_value=plan)
            response = client.post("/api/map/route", json={"origin_poi_id": "origin", "destination_poi_id": "destination"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["distance_m"], 1000)
            self.assertEqual(client.post("/api/map/route", json={}).status_code, 422)

    def test_expected_error_statuses(self):
        for error, status in [
            (AmapConfigurationError("missing key"), 503),
            (AmapRouteNotFoundError("no route"), 404),
            (AmapServiceError("upstream error"), 502),
        ]:
            with self.subTest(status=status), TestClient(app) as client, patch("app.api.main.AmapService") as service:
                service.return_value.plan_route = AsyncMock(side_effect=error)
                response = client.post("/api/map/route", json={"origin_poi_id": "origin", "destination_poi_id": "destination"})
                self.assertEqual(response.status_code, status)


if __name__ == "__main__":
    unittest.main()
