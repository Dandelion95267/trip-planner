"""地图补充坐标接口的测试，不调用外部高德服务。"""

import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.api.main import app
from app.models.schemas import POIInfo
from app.services.amap_service import AmapConfigurationError, AmapServiceError


class PoiDetailTests(unittest.TestCase):
    def test_returns_normalized_detail(self):
        with TestClient(app) as client, patch("app.api.main.AmapService") as service:
            service.return_value.get_poi_detail = AsyncMock(return_value=POIInfo(
                id="test-id", name="地点", location={"longitude": 116.4, "latitude": 39.9},
            ))
            response = client.get("/api/map/poi/test-id")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["location"]["longitude"], 116.4)
            service.return_value.get_poi_detail.assert_awaited_once_with("test-id")

    def test_invalid_id_is_rejected_before_service_call(self):
        with TestClient(app) as client, patch("app.api.main.AmapService") as service:
            self.assertEqual(client.get("/api/map/poi/invalid!").status_code, 422)
            self.assertEqual(client.get("/api/map/poi/" + "a" * 101).status_code, 422)
            service.assert_not_called()

    def test_configuration_and_service_errors(self):
        for error, status in [(AmapConfigurationError("missing key"), 503), (AmapServiceError("unavailable"), 502)]:
            with TestClient(app) as client, patch("app.api.main.AmapService") as service:
                service.return_value.get_poi_detail = AsyncMock(side_effect=error)
                self.assertEqual(client.get("/api/map/poi/test-id").status_code, status)


if __name__ == "__main__":
    unittest.main()
