"""使用模拟服务验证地点来源和接口行为，不调用外部 API。"""

import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

from app.agents.trip_planner_agent import TripPlannerAgent, TripPlanningError
from app.api.main import app
from app.models.schemas import POIInfo, RoutePlan, TripRequest, WeatherInfo
from app.services.amap_service import AmapConfigurationError, AmapServiceError


def make_request():
    return TripRequest(
        city="北京", start_date="2026-11-01", end_date="2026-11-01",
        travel_days=1, transportation="公共交通", accommodation="经济型酒店",
        preferences=["历史文化"],
    )


def model_response():
    return {
        "city": "北京", "start_date": "2026-11-01", "end_date": "2026-11-01",
        "days": [{
            "date": "2026-11-01", "day_index": 0,
            "description": "上午参观博物馆，下午自由活动，晚间品尝当地美食。",
            "transportation": "公共交通", "accommodation": "经济型酒店",
            "attractions": [{
                "poi_id": "verified-id", "name": "模型改写的名称", "address": "模型生成的地址",
                "location": {"longitude": 0, "latitude": 0},
                "visit_duration": 120, "description": "建议参观两小时。",
                "rating": 5, "ticket_price": 100,
            }],
        }],
        "overall_suggestions": "出发前确认开放时间和预约要求。",
        "budget": {"total": 100},
    }


def make_planner():
    maps = Mock()
    maps.search_poi = AsyncMock(return_value=[POIInfo(
        id="verified-id", name="高德返回的博物馆", address="高德返回的地址", type="博物馆",
    )])
    maps.get_weather = AsyncMock(return_value=[])
    maps.search_hotels = AsyncMock(return_value=[])
    maps.search_restaurants = AsyncMock(return_value=[])
    maps.plan_route = AsyncMock(side_effect=AmapServiceError("test route unavailable"))
    maps.get_poi_detail = AsyncMock(side_effect=AmapServiceError("test detail unavailable"))
    return TripPlannerAgent(llm=Mock(), amap_service=maps)


def make_weather(forecast_date):
    return WeatherInfo(
        date=forecast_date, day_weather="小雨", night_weather="阴",
        day_temp=15, night_temp=5, wind_direction="北", wind_power="1-3",
    )


def make_routed_planner():
    planner = make_planner()
    planner.amap_service.search_poi.return_value.append(POIInfo(
        id="second-id", name="第二个真实景点", address="第二个地址",
    ))
    response = model_response()
    response["days"][0]["attractions"].append({
        "poi_id": "second-id", "name": "模型名称", "address": "模型地址",
        "visit_duration": 60, "description": "参观建议。",
    })
    route = RoutePlan(
        origin=POIInfo(id="verified-id", name="起点", location={"longitude": 116.4, "latitude": 39.9}),
        destination=POIInfo(id="second-id", name="终点", location={"longitude": 116.5, "latitude": 39.95}),
        mode="transit", duration_s=600, walking_distance_m=100, steps=["乘坐公交"],
    )
    planner.amap_service.plan_route.side_effect = None
    planner.amap_service.plan_route.return_value = route
    return planner, response, route


def make_order_planner():
    planner = make_planner()
    details = {
        name: POIInfo(id=name, name=f"景点{name.upper()}", address=f"地址{name}",
                      location={"longitude": longitude, "latitude": 39.9})
        for name, longitude in [("a", 116.4), ("b", 116.42), ("c", 116.41)]
    }
    planner.amap_service.search_poi.return_value = [place.model_copy(update={"location": None}) for place in details.values()]
    planner.amap_service.get_poi_detail.side_effect = lambda poi_id: details[poi_id].model_copy(deep=True)
    planner.amap_service.plan_route.side_effect = lambda query: RoutePlan(
        origin=details[query.origin_poi_id], destination=details[query.destination_poi_id],
        mode=query.mode, distance_m=1000, duration_s=600,
    )
    response = model_response()
    response["days"][0]["description"] = "旧摘要：景点A → 景点B → 景点C。"
    response["days"][0]["attractions"] = [
        {"poi_id": place.id, "name": place.name, "address": place.address,
         "visit_duration": 60, "description": "游览建议。"} for place in details.values()
    ]
    return planner, response, details


class TripPlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_verified_places_and_preserves_unknown_fields(self):
        planner = make_planner()
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.return_value = json.dumps(model_response())
            plan = await planner.plan_trip(make_request())
            prompt = json.loads(agent_class.return_value.run.call_args.args[0])
            self.assertEqual(prompt["高德候选景点"][0]["id"], "verified-id")
        planner.amap_service.search_poi.assert_awaited_once_with(keywords="旅游景点", city="北京")
        attraction = plan.days[0].attractions[0]
        self.assertEqual(attraction.name, "高德返回的博物馆")
        self.assertEqual(attraction.address, "高德返回的地址")
        self.assertEqual(attraction.visit_duration, 120)
        self.assertIsNone(attraction.location)
        self.assertIsNone(attraction.ticket_price)
        self.assertIsNone(attraction.rating)
        self.assertIsNone(plan.budget)
        self.assertEqual(plan.days[0].routes, [])
        planner.amap_service.plan_route.assert_not_awaited()

    async def test_rejects_unverified_repeated_or_missing_places(self):
        for case in ["unknown_id", "duplicate", "empty", "wrong_date", "invalid_json"]:
            with self.subTest(case=case):
                response = model_response()
                attractions = response["days"][0]["attractions"]
                if case == "unknown_id":
                    attractions[0]["poi_id"] = "made-up-id"
                elif case == "duplicate":
                    attractions.append(attractions[0].copy())
                elif case == "empty":
                    attractions.clear()
                elif case == "wrong_date":
                    response["days"][0]["date"] = "2026-11-02"
                text = "not JSON" if case == "invalid_json" else json.dumps(response)
                with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
                    agent_class.return_value.run.return_value = text
                    with self.assertRaises(TripPlanningError):
                        await make_planner().plan_trip(make_request())

    async def test_empty_search_stops_before_model_call(self):
        planner = make_planner()
        planner.amap_service.search_poi.return_value = []
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            with self.assertRaises(TripPlanningError):
                await planner.plan_trip(make_request())
            agent_class.assert_not_called()

    async def test_model_failure_returns_safe_error(self):
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.side_effect = RuntimeError("private provider details")
            with self.assertRaises(TripPlanningError) as caught:
                await make_planner().plan_trip(make_request())
            self.assertNotIn("private", str(caught.exception))

    async def test_only_matching_forecasts_reach_model_and_response(self):
        planner = make_planner()
        forecast = make_weather("2026-11-01")
        planner.amap_service.get_weather.return_value = [make_weather("2026-10-31"), forecast]
        response = model_response()
        # 即使模型输出了其他日期的天气，最终也只采用高德对应日期的预报。
        response["weather_info"] = [make_weather("2026-12-01").model_dump()]
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.return_value = json.dumps(response)
            plan = await planner.plan_trip(make_request())
            prompt = json.loads(agent_class.return_value.run.call_args.args[0])
        self.assertEqual(prompt["高德天气预报"], [forecast.model_dump()])
        self.assertEqual(prompt["暂无天气预报的日期"], [])
        self.assertEqual(plan.weather_info, [forecast])

    async def test_partial_forecast_leaves_uncovered_days_unknown(self):
        planner = make_planner()
        planner.amap_service.get_weather.return_value = [make_weather("2026-11-01")]
        request = make_request().model_copy(update={"end_date": "2026-11-02", "travel_days": 2})
        response = model_response()
        second_day = json.loads(json.dumps(response["days"][0]))
        second_day.update(date="2026-11-02", day_index=1)
        response["days"].append(second_day)
        response["end_date"] = "2026-11-02"
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.return_value = json.dumps(response)
            plan = await planner.plan_trip(request)
        self.assertEqual([item.date for item in plan.weather_info], ["2026-11-01"])
        self.assertIn("有1天暂无天气预报", plan.overall_suggestions)

    async def test_missing_or_failed_weather_still_returns_plan_with_notice(self):
        for failure in [False, True]:
            with self.subTest(failure=failure):
                planner = make_planner()
                if failure:
                    planner.amap_service.get_weather.side_effect = AmapServiceError("private details")
                with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
                    agent_class.return_value.run.return_value = json.dumps(model_response())
                    plan = await planner.plan_trip(make_request())
                self.assertEqual(plan.weather_info, [])
                self.assertIn("天气", plan.overall_suggestions)
                self.assertNotIn("private", plan.overall_suggestions)

    async def test_selected_hotel_uses_map_facts_and_unknown_prices(self):
        planner = make_planner()
        planner.amap_service.search_hotels.return_value = [POIInfo(
            id="hotel-id", name="地图中的酒店", address="地图中的酒店地址",
        )]
        response = model_response()
        response["days"][0]["hotel"] = {
            "poi_id": "hotel-id", "name": "模型改写名称", "address": "模型地址",
            "location": {"longitude": 0, "latitude": 0}, "price_range": "100-200元",
            "rating": "5", "distance": "100米", "estimated_cost": 150,
        }
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.return_value = json.dumps(response)
            plan = await planner.plan_trip(make_request())
            prompt = json.loads(agent_class.return_value.run.call_args.args[0])
        self.assertEqual(prompt["高德候选酒店"][0]["id"], "hotel-id")
        planner.amap_service.search_hotels.assert_awaited_once_with(city="北京", preference="经济型酒店")
        hotel = plan.days[0].hotel
        self.assertEqual(hotel.name, "地图中的酒店")
        self.assertEqual(hotel.address, "地图中的酒店地址")
        for field in ["location", "price_range", "rating", "distance", "estimated_cost"]:
            self.assertIsNone(getattr(hotel, field))
        self.assertIn("房价、房态", plan.overall_suggestions)

    async def test_rejects_unknown_or_missing_hotel_selection(self):
        for case in ["unknown", "missing"]:
            with self.subTest(case=case):
                planner = make_planner()
                planner.amap_service.search_hotels.return_value = [POIInfo(id="hotel-id", name="酒店")]
                response = model_response()
                if case == "unknown":
                    response["days"][0]["hotel"] = {"poi_id": "made-up-id", "name": "编造酒店"}
                with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
                    agent_class.return_value.run.return_value = json.dumps(response)
                    with self.assertRaises(TripPlanningError):
                        await planner.plan_trip(make_request())

    async def test_failed_hotel_lookup_keeps_plan_and_adds_notice(self):
        planner = make_planner()
        planner.amap_service.search_hotels.side_effect = AmapServiceError("private details")
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.return_value = json.dumps(model_response())
            plan = await planner.plan_trip(make_request())
        self.assertIsNone(plan.days[0].hotel)
        self.assertIn("住宿查询暂不可用", plan.overall_suggestions)
        self.assertNotIn("private", plan.overall_suggestions)

    async def test_meals_use_verified_places_and_clear_unverified_costs(self):
        planner = make_planner()
        planner.amap_service.search_restaurants.return_value = [POIInfo(
            id="restaurant-id", name="地图中的餐馆", address="地图中的餐馆地址",
            location={"longitude": 116.4, "latitude": 39.9},
        )]
        response = model_response()
        response["days"][0]["meals"] = [{
            "type": meal_type, "poi_id": "restaurant-id", "name": "模型改写名称",
            "address": "模型地址", "estimated_cost": 100,
        } for meal_type in ("dinner", "lunch")]
        request = make_request().model_copy(update={"dining_preference": "面食"})
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.return_value = json.dumps(response)
            plan = await planner.plan_trip(request)
            prompt = json.loads(agent_class.return_value.run.call_args.args[0])
        planner.amap_service.search_restaurants.assert_awaited_once_with(city="北京", preference="面食")
        self.assertEqual(prompt["高德候选餐馆"][0]["id"], "restaurant-id")
        self.assertEqual([meal.type for meal in plan.days[0].meals], ["lunch", "dinner"])
        for meal in plan.days[0].meals:
            self.assertEqual(meal.name, "地图中的餐馆")
            self.assertEqual(meal.address, "地图中的餐馆地址")
            self.assertEqual(meal.location.longitude, 116.4)
            self.assertIsNone(meal.estimated_cost)
        self.assertIn("菜单、营业时间", plan.overall_suggestions)

    async def test_rejects_invalid_restaurant_or_meal_slot(self):
        for case in ["unknown_id", "missing_meal", "duplicate_slot", "invalid_type", "no_candidates"]:
            with self.subTest(case=case):
                planner = make_planner()
                if case != "no_candidates":
                    planner.amap_service.search_restaurants.return_value = [POIInfo(id="restaurant-id", name="餐馆")]
                response = model_response()
                meals = [{"type": slot, "poi_id": "restaurant-id", "name": "餐馆"} for slot in ("lunch", "dinner")]
                if case == "unknown_id":
                    meals[0]["poi_id"] = "made-up-id"
                elif case == "missing_meal":
                    meals.pop()
                elif case == "duplicate_slot":
                    meals[1]["type"] = "lunch"
                elif case == "invalid_type":
                    meals[0]["type"] = "midnight"
                response["days"][0]["meals"] = meals
                with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
                    agent_class.return_value.run.return_value = json.dumps(response)
                    with self.assertRaises(TripPlanningError):
                        await planner.plan_trip(make_request())

    async def test_restaurant_failure_still_returns_plan_and_notice(self):
        planner = make_planner()
        planner.amap_service.search_restaurants.side_effect = AmapServiceError("private details")
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.return_value = json.dumps(model_response())
            plan = await planner.plan_trip(make_request())
        self.assertEqual(plan.days[0].meals, [])
        self.assertIn("餐馆查询暂不可用", plan.overall_suggestions)
        self.assertNotIn("private", plan.overall_suggestions)

    async def test_routes_are_queried_after_planning_and_model_routes_are_discarded(self):
        planner, response, route = make_routed_planner()
        response["days"][0]["routes"] = [route.model_copy(update={"duration_s": 99999}).model_dump()]
        response["days"][0]["route_warnings"] = ["模型编写的提示"]
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.return_value = json.dumps(response)
            plan = await planner.plan_trip(make_request())
            agent_class.return_value.run.assert_called_once()
        self.assertEqual(plan.days[0].routes[0].duration_s, 600)
        self.assertEqual(plan.days[0].route_warnings, [])
        self.assertEqual(plan.days[0].attractions[0].location.longitude, 116.4)
        self.assertEqual(plan.days[0].attractions[1].location.longitude, 116.5)
        query = planner.amap_service.plan_route.await_args.args[0]
        self.assertEqual((query.origin_poi_id, query.destination_poi_id, query.mode, query.city),
                         ("verified-id", "second-id", "transit", "北京"))

    async def test_explicit_route_mode_and_unsupported_transport(self):
        for explicit_mode in ["walking", None]:
            with self.subTest(explicit_mode=explicit_mode):
                planner, response, route = make_routed_planner()
                planner.amap_service.plan_route.return_value = route.model_copy(update={"mode": "walking"})
                request = make_request().model_copy(update={"transportation": "骑行", "route_mode": explicit_mode})
                with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
                    agent_class.return_value.run.return_value = json.dumps(response)
                    plan = await planner.plan_trip(request)
                if explicit_mode:
                    self.assertEqual(plan.days[0].routes[0].mode, "walking")
                else:
                    planner.amap_service.plan_route.assert_not_awaited()
                    self.assertEqual(plan.days[0].routes, [])
                    self.assertTrue(plan.days[0].route_warnings)

    async def test_failed_or_timed_out_routes_keep_itinerary_with_safe_warning(self):
        for error in [AmapServiceError("private details"), TimeoutError()]:
            with self.subTest(error=type(error).__name__):
                planner, response, _ = make_routed_planner()
                planner.amap_service.plan_route.side_effect = error
                with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
                    agent_class.return_value.run.return_value = json.dumps(response)
                    plan = await planner.plan_trip(make_request())
                self.assertEqual(len(plan.days[0].attractions), 2)
                self.assertEqual(plan.days[0].routes, [])
                self.assertEqual(len(plan.days[0].route_warnings), 1)
                self.assertNotIn("private", json.dumps(plan.model_dump()))

    async def test_partial_failure_preserves_successful_route(self):
        planner, response, route = make_routed_planner()
        third = POIInfo(id="third-id", name="第三个景点")
        planner.amap_service.search_poi.return_value.append(third)
        response["days"][0]["attractions"].append({
            "poi_id": third.id, "name": third.name, "address": "", "visit_duration": 60, "description": "游览建议。",
        })
        next_route = route.model_copy(update={"origin": route.destination, "destination": third})
        async def lookup(query):
            if (query.origin_poi_id, query.destination_poi_id) == ("second-id", third.id):
                return next_route
            raise AmapServiceError("failed route")
        planner.amap_service.plan_route.side_effect = lookup
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.return_value = json.dumps(response)
            plan = await planner.plan_trip(make_request())
        self.assertEqual(len(plan.days[0].routes), 1)
        self.assertEqual(plan.days[0].routes[0].origin.id, "second-id")
        self.assertEqual(len(plan.days[0].route_warnings), 1)

    async def test_repeated_leg_is_queried_once_per_trip(self):
        planner, response, _ = make_routed_planner()
        request = make_request().model_copy(update={"end_date": "2026-11-02", "travel_days": 2})
        second_day = json.loads(json.dumps(response["days"][0]))
        second_day.update(date="2026-11-02", day_index=1)
        response["days"].append(second_day)
        response["end_date"] = "2026-11-02"
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.return_value = json.dumps(response)
            plan = await planner.plan_trip(request)
        planner.amap_service.plan_route.assert_awaited_once()
        self.assertEqual([len(day.routes) for day in plan.days], [1, 1])
        self.assertIsNot(plan.days[0].routes[0], plan.days[1].routes[0])

    async def test_route_for_wrong_endpoint_is_not_attached(self):
        planner, response, route = make_routed_planner()
        planner.amap_service.plan_route.return_value = route.model_copy(update={"destination": route.origin})
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.return_value = json.dumps(response)
            plan = await planner.plan_trip(make_request())
        self.assertEqual(plan.days[0].routes, [])
        self.assertTrue(plan.days[0].route_warnings)

    async def test_auto_prefers_short_walk_and_compares_above_either_limit(self):
        for distance, duration, expected_calls, expected_mode in [
            (1500, 1200, 1, "walking"), (1501, 1200, 2, "transit"), (1500, 1201, 2, "transit"),
        ]:
            with self.subTest(distance=distance, duration=duration):
                planner, response, route = make_routed_planner()
                walking = route.model_copy(update={"mode": "walking", "distance_m": distance, "duration_s": duration})
                transit = route.model_copy(update={"duration_s": 900})
                planner.amap_service.plan_route.side_effect = [walking, transit]
                request = make_request().model_copy(update={"route_mode": "auto"})
                with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
                    agent_class.return_value.run.return_value = json.dumps(response)
                    plan = await planner.plan_trip(request)
                self.assertEqual(planner.amap_service.plan_route.await_count, expected_calls)
                self.assertEqual(plan.days[0].routes[0].mode, expected_mode)
                self.assertTrue(plan.days[0].routes[0].selection_reason)

    async def test_auto_compares_current_duration_and_explains_choice(self):
        for transit_duration, expected_mode in [(600, "transit"), (2400, "walking"), (3000, "walking")]:
            with self.subTest(transit_duration=transit_duration):
                planner, response, route = make_routed_planner()
                walking = route.model_copy(update={"mode": "walking", "distance_m": 3000, "duration_s": 2400})
                transit = route.model_copy(update={"duration_s": transit_duration})
                planner.amap_service.plan_route.side_effect = [walking, transit]
                with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
                    agent_class.return_value.run.return_value = json.dumps(response)
                    plan = await planner.plan_trip(make_request().model_copy(update={"route_mode": "auto"}))
                selected = plan.days[0].routes[0]
                self.assertEqual(selected.mode, expected_mode)
                self.assertIn("按当前预计耗时", selected.selection_reason)
                if expected_mode == "walking":
                    self.assertTrue(selected.warnings)
                    self.assertTrue(plan.days[0].route_warnings)
                self.assertEqual([a.poi_id for a in plan.days[0].attractions], ["verified-id", "second-id"])

    async def test_auto_single_available_option_is_not_claimed_as_full_comparison(self):
        for walking_fails in [False, True]:
            with self.subTest(walking_fails=walking_fails):
                planner, response, route = make_routed_planner()
                walking = route.model_copy(update={"mode": "walking", "distance_m": 3000, "duration_s": 2400})
                failure = AmapServiceError("private provider details")
                planner.amap_service.plan_route.side_effect = [failure, route] if walking_fails else [walking, failure]
                with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
                    agent_class.return_value.run.return_value = json.dumps(response)
                    plan = await planner.plan_trip(make_request().model_copy(update={"route_mode": "auto"}))
                selected = plan.days[0].routes[0]
                self.assertEqual(selected.mode, "transit" if walking_fails else "walking")
                self.assertTrue(any("未完成" in warning for warning in selected.warnings))
                self.assertNotIn("private", json.dumps(plan.model_dump()))

    async def test_auto_both_queries_fail_without_discarding_itinerary(self):
        planner, response, _ = make_routed_planner()
        planner.amap_service.plan_route.side_effect = AmapServiceError("no route")
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.return_value = json.dumps(response)
            plan = await planner.plan_trip(make_request().model_copy(update={"route_mode": "auto"}))
        self.assertEqual(len(plan.days[0].attractions), 2)
        self.assertEqual(plan.days[0].routes, [])
        self.assertTrue(plan.days[0].route_warnings)
        # 两种方式都失败后，还会有限尝试反向安排；每个方向只查一次。
        self.assertEqual(planner.amap_service.plan_route.await_count, 4)

    async def test_auto_alias_updates_daily_transportation_from_actual_selection(self):
        planner, response, route = make_routed_planner()
        planner.amap_service.plan_route.return_value = route.model_copy(update={
            "mode": "walking", "distance_m": 1000, "duration_s": 800,
        })
        request = make_request().model_copy(update={"transportation": "自动"})
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.return_value = json.dumps(response)
            plan = await planner.plan_trip(request)
        self.assertEqual(plan.days[0].transportation, "景点间交通建议：步行")
        planner.amap_service.plan_route.assert_awaited_once()

    async def test_order_optimization_preserves_points_and_queries_new_route_order(self):
        planner, response, _ = make_order_planner()
        request = make_request().model_copy(update={"optimize_order": True, "route_mode": "walking"})
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.return_value = json.dumps(response)
            plan = await planner.plan_trip(request)
            agent_class.return_value.run.assert_called_once()
        day = plan.days[0]
        self.assertEqual([place.poi_id for place in day.attractions], ["a", "c", "b"])
        self.assertEqual(day.order_optimization.status, "optimized")
        self.assertEqual(day.order_optimization.distance_basis, "straight_line")
        self.assertLess(day.order_optimization.after_distance_m, day.order_optimization.before_distance_m)
        self.assertNotIn("旧摘要", day.description)
        self.assertIn("景点A → 景点C → 景点B", day.description)
        self.assertEqual([(route.origin.id, route.destination.id) for route in day.routes], [("a", "c"), ("c", "b")])
        self.assertEqual(day.date, request.start_date)

    async def test_order_optimization_off_does_not_trust_model_optimization(self):
        planner, response, _ = make_order_planner()
        response["days"][0]["order_optimization"] = {"status": "optimized", "reason": "模型自行声称已优化"}
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.return_value = json.dumps(response)
            plan = await planner.plan_trip(make_request())
        self.assertEqual([place.poi_id for place in plan.days[0].attractions], ["a", "b", "c"])
        self.assertIsNone(plan.days[0].order_optimization)
        planner.amap_service.get_poi_detail.assert_not_awaited()

    async def test_missing_coordinates_skip_order_change_and_keep_plan(self):
        for failure in [AmapServiceError("private details"), TimeoutError(), None, "wrong_id"]:
            with self.subTest(failure=type(failure).__name__):
                planner, response, _ = make_order_planner()
                if isinstance(failure, Exception):
                    planner.amap_service.get_poi_detail.side_effect = failure
                else:
                    planner.amap_service.get_poi_detail.side_effect = None
                    planner.amap_service.get_poi_detail.return_value = POIInfo(
                        id="wrong" if failure else "a", name="地点",
                    )
                planner.amap_service.plan_route.side_effect = AmapServiceError("route unavailable")
                with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
                    agent_class.return_value.run.return_value = json.dumps(response)
                    plan = await planner.plan_trip(make_request().model_copy(update={"optimize_order": True}))
                day = plan.days[0]
                self.assertEqual([place.poi_id for place in day.attractions], ["a", "b", "c"])
                self.assertEqual(day.order_optimization.status, "skipped")
                self.assertIsNone(day.order_optimization.before_distance_m)
                self.assertNotIn("private", json.dumps(plan.model_dump()))

    async def test_already_good_order_preserves_description(self):
        planner, response, _ = make_order_planner()
        day = response["days"][0]
        day["attractions"] = [day["attractions"][i] for i in [0, 2, 1]]
        day["description"] = "正确摘要：景点A → 景点C → 景点B。"
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.return_value = json.dumps(response)
            plan = await planner.plan_trip(make_request().model_copy(update={"optimize_order": True}))
        self.assertEqual(plan.days[0].order_optimization.status, "unchanged")
        self.assertEqual(plan.days[0].description, day["description"])

    async def test_two_points_do_not_need_order_queries(self):
        planner, response, _ = make_routed_planner()
        with patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.return_value = json.dumps(response)
            plan = await planner.plan_trip(make_request().model_copy(update={"optimize_order": True}))
        self.assertEqual(plan.days[0].order_optimization.status, "skipped")
        planner.amap_service.get_poi_detail.assert_not_awaited()


class TripRouteTests(unittest.TestCase):
    def test_order_optimization_is_available_through_trip_endpoint(self):
        planner, response, _ = make_order_planner()
        request = make_request().model_dump()
        request["optimize_order"] = True
        with TestClient(app) as client, patch("app.api.main.TripPlannerAgent", return_value=planner), \
             patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.return_value = json.dumps(response)
            result = client.post("/api/trip/plan", json=request)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["days"][0]["order_optimization"]["status"], "optimized")

    def test_auto_mode_is_accepted_by_trip_endpoint(self):
        planner, response, route = make_routed_planner()
        planner.amap_service.plan_route.return_value = route.model_copy(update={
            "mode": "walking", "distance_m": 1000, "duration_s": 800,
        })
        request = make_request().model_dump()
        request["route_mode"] = "auto"
        with TestClient(app) as client, patch("app.api.main.TripPlannerAgent", return_value=planner), \
             patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.return_value = json.dumps(response)
            result = client.post("/api/trip/plan", json=request)
        self.assertEqual(result.status_code, 200)
        self.assertIn("优先步行", result.json()["days"][0]["routes"][0]["selection_reason"])

    def test_route_returns_grounded_plan(self):
        with TestClient(app) as client, \
             patch("app.api.main.TripPlannerAgent", return_value=make_planner()), \
             patch("app.agents.trip_planner_agent.SimpleAgent") as agent_class:
            agent_class.return_value.run.return_value = json.dumps(model_response())
            response = client.post("/api/trip/plan", json=make_request().model_dump())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["days"][0]["attractions"][0]["name"], "高德返回的博物馆")

    def test_map_errors_and_invalid_input(self):
        for error, status in [(AmapConfigurationError("missing key"), 503),
                              (AmapServiceError("search failed"), 502)]:
            with self.subTest(status=status):
                planner = make_planner()
                planner.amap_service.search_poi.side_effect = error
                with TestClient(app) as client, patch("app.api.main.TripPlannerAgent", return_value=planner):
                    response = client.post("/api/trip/plan", json=make_request().model_dump())
                self.assertEqual(response.status_code, status)
        invalid = make_request().model_dump()
        invalid["travel_days"] = 2
        with TestClient(app) as client, patch("app.api.main.TripPlannerAgent") as agent_class:
            self.assertEqual(client.post("/api/trip/plan", json=invalid).status_code, 422)
            agent_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()
