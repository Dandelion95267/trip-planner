"""天气解析与接口测试，不发送外部网络请求。"""

import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from mcp.types import CallToolResult, TextContent

from app.api.main import app
from app.services.amap_service import AmapConfigurationError, AmapService, AmapServiceError


def forecast_data():
    return {"city": "北京市", "forecasts": [{
        "date": "2026-11-01", "dayweather": "晴", "nightweather": "多云",
        "daytemp": "17", "nighttemp": "-2℃", "daywind": "北", "nightwind": "南",
        "daypower": "1-3", "nightpower": "3-4",
    }]}


class WeatherParsingTests(unittest.TestCase):
    def test_text_and_structured_results(self):
        for result in [
            CallToolResult(content=[], structuredContent=forecast_data()),
            CallToolResult(content=[TextContent(type="text", text=json.dumps(forecast_data()))]),
        ]:
            weather = AmapService._parse_weather(result)
            self.assertEqual(weather[0].date, "2026-11-01")
            self.assertEqual(weather[0].day_temp, 17)
            self.assertEqual(weather[0].night_temp, -2)
            self.assertEqual(weather[0].wind_direction, "白天北，夜间南")

    def test_empty_forecast_and_invalid_data(self):
        self.assertEqual(AmapService._parse_weather(
            CallToolResult(content=[], structuredContent={"forecasts": []})
        ), [])
        for case in ["missing", "bad_date", "unknown_temp", "duplicate", "rejected"]:
            with self.subTest(case=case):
                data = forecast_data()
                if case == "missing":
                    del data["forecasts"]
                elif case == "bad_date":
                    data["forecasts"][0]["date"] = "2026-02-30"
                elif case == "unknown_temp":
                    data["forecasts"][0]["daytemp"] = "未知"
                elif case == "duplicate":
                    data["forecasts"].append(data["forecasts"][0].copy())
                elif case == "rejected":
                    data["status"] = "0"
                with self.assertRaises(ValueError):
                    AmapService._parse_weather(CallToolResult(content=[], structuredContent=data))


class WeatherServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_correct_mcp_tool_is_called(self):
        result = CallToolResult(content=[], structuredContent=forecast_data())
        with patch.object(AmapService, "_call_tool", new_callable=AsyncMock, return_value=result) as call:
            weather = await AmapService().get_weather("北京")
        call.assert_awaited_once_with("maps_weather", {"city": "北京"})
        self.assertEqual(len(weather), 1)

    async def test_poi_search_still_uses_same_tool_and_parser(self):
        result = CallToolResult(content=[], structuredContent={"pois": [
            {"id": "poi-id", "name": "地点", "address": "地址", "location": "116.4,39.9"}
        ]})
        with patch.object(AmapService, "_call_tool", new_callable=AsyncMock, return_value=result) as call:
            places = await AmapService().search_poi("景点", "北京")
        call.assert_awaited_once_with("maps_text_search", {"keywords": "景点", "city": "北京", "citylimit": "true"})
        self.assertEqual(places[0].location.longitude, 116.4)


class WeatherRouteTests(unittest.TestCase):
    def test_success_and_blank_city(self):
        weather = AmapService._parse_weather(CallToolResult(content=[], structuredContent=forecast_data()))
        with TestClient(app) as client, patch("app.api.main.AmapService") as service:
            service.return_value.get_weather = AsyncMock(return_value=weather)
            response = client.get("/api/map/weather", params={"city": " 北京 "})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()[0]["day_temp"], 17)
            service.return_value.get_weather.assert_awaited_once_with(city="北京")
            self.assertEqual(client.get("/api/map/weather", params={"city": " "}).status_code, 422)
            self.assertEqual(client.get("/api/map/weather").status_code, 422)

    def test_configuration_and_service_errors(self):
        for error, status in [(AmapConfigurationError("missing key"), 503),
                              (AmapServiceError("weather failed"), 502)]:
            with TestClient(app) as client, patch("app.api.main.AmapService") as service:
                service.return_value.get_weather = AsyncMock(side_effect=error)
                self.assertEqual(client.get("/api/map/weather", params={"city": "北京"}).status_code, status)


if __name__ == "__main__":
    unittest.main()
