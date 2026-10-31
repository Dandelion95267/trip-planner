"""餐馆搜索与接口测试，不发送真实网络请求。"""

import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.api.main import app
from app.models.schemas import POIInfo
from app.services.amap_service import AmapConfigurationError, AmapService, AmapServiceError


class RestaurantSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_keywords_include_preference_and_restaurant_category(self):
        for preference, keywords in [(" ", "餐馆"), ("面食", "面食 餐馆"), ("本地小吃", "本地小吃")]:
            with self.subTest(preference=preference):
                places = [POIInfo(id="restaurant-id", name="餐馆")]
                with patch.object(AmapService, "search_poi", new_callable=AsyncMock, return_value=places) as search:
                    result = await AmapService().search_restaurants("北京", preference)
                self.assertEqual(result, places)
                search.assert_awaited_once_with(keywords=keywords, city="北京")

    async def test_no_results_does_not_drop_dining_preference(self):
        with patch.object(AmapService, "search_poi", new_callable=AsyncMock, return_value=[]) as search:
            self.assertEqual(await AmapService().search_restaurants("北京", "面食"), [])
        search.assert_awaited_once_with(keywords="面食 餐馆", city="北京")


class RestaurantRouteTests(unittest.TestCase):
    def test_success_and_input_validation(self):
        with TestClient(app) as client, patch("app.api.main.AmapService") as service:
            service.return_value.search_restaurants = AsyncMock(return_value=[POIInfo(id="restaurant-id", name="餐馆")])
            response = client.get("/api/map/restaurants", params={"city": " 北京 ", "preference": "面食"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()[0]["id"], "restaurant-id")
            service.return_value.search_restaurants.assert_awaited_once_with(city="北京", preference="面食")
            self.assertEqual(client.get("/api/map/restaurants", params={"city": " "}).status_code, 422)
            self.assertEqual(client.get("/api/map/restaurants").status_code, 422)

    def test_configuration_and_service_errors(self):
        for error, status in [(AmapConfigurationError("missing key"), 503), (AmapServiceError("failed"), 502)]:
            with TestClient(app) as client, patch("app.api.main.AmapService") as service:
                service.return_value.search_restaurants = AsyncMock(side_effect=error)
                self.assertEqual(client.get("/api/map/restaurants", params={"city": "北京"}).status_code, status)


if __name__ == "__main__":
    unittest.main()
