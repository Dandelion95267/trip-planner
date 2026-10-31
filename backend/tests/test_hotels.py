"""住宿搜索与接口测试，不发送真实网络请求。"""

import unittest
from unittest.mock import AsyncMock, call, patch

from fastapi.testclient import TestClient

from app.api.main import app
from app.models.schemas import POIInfo
from app.services.amap_service import AmapConfigurationError, AmapService, AmapServiceError


class HotelSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_with_preference_and_default_keyword(self):
        for preference, keyword in [("经济型酒店", "经济型酒店"), ("安静", "安静 酒店"), (" ", "酒店")]:
            with self.subTest(preference=preference):
                places = [POIInfo(id="hotel-id", name="住宿地点")]
                with patch.object(AmapService, "search_poi", new_callable=AsyncMock, return_value=places) as search:
                    self.assertEqual(await AmapService().search_hotels("北京", preference), places)
                search.assert_awaited_once_with(keywords=keyword, city="北京")

    async def test_empty_preference_search_retries_general_hotels(self):
        places = [POIInfo(id="hotel-id", name="住宿地点")]
        with patch.object(AmapService, "search_poi", new_callable=AsyncMock, side_effect=[[], places]) as search:
            self.assertEqual(await AmapService().search_hotels("北京", "安静"), places)
        self.assertEqual(search.await_args_list, [
            call(keywords="安静 酒店", city="北京"), call(keywords="酒店", city="北京")
        ])

    async def test_empty_general_search_does_not_repeat(self):
        with patch.object(AmapService, "search_poi", new_callable=AsyncMock, return_value=[]) as search:
            self.assertEqual(await AmapService().search_hotels("北京"), [])
        search.assert_awaited_once()


class HotelRouteTests(unittest.TestCase):
    def test_returns_places_and_rejects_blank_city(self):
        with TestClient(app) as client, patch("app.api.main.AmapService") as service:
            service.return_value.search_hotels = AsyncMock(return_value=[POIInfo(id="hotel-id", name="住宿地点")])
            response = client.get("/api/map/hotels", params={"city": " 北京 ", "preference": "经济型酒店"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()[0]["id"], "hotel-id")
            service.return_value.search_hotels.assert_awaited_once_with(city="北京", preference="经济型酒店")
            self.assertEqual(client.get("/api/map/hotels", params={"city": " "}).status_code, 422)
            self.assertEqual(client.get("/api/map/hotels").status_code, 422)

    def test_configuration_and_service_errors(self):
        for error, status in [(AmapConfigurationError("missing key"), 503), (AmapServiceError("failed"), 502)]:
            with TestClient(app) as client, patch("app.api.main.AmapService") as service:
                service.return_value.search_hotels = AsyncMock(side_effect=error)
                response = client.get("/api/map/hotels", params={"city": "北京"})
                self.assertEqual(response.status_code, status)


if __name__ == "__main__":
    unittest.main()
