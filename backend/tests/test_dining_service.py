"""固定地图数据验证餐馆选择、缺失处理、预算和跨修改轮次复用。"""

import unittest
from unittest.mock import AsyncMock, patch

from mcp.types import CallToolResult

from app.models.schemas import POIInfo, RoutePlan, TripPlan, TripRequest
from app.services.amap_service import AmapService, AmapServiceError
from app.services.dining_service import DiningService, DiningState


def inputs(keys="AB"):
    req = TripRequest(city="北京", start_date="2026-11-05", end_date="2026-11-05", travel_days=1,
                      transportation="公共交通", accommodation="酒店", dining_preference="清淡")
    p = TripPlan(city=req.city, start_date=req.start_date, end_date=req.end_date,
                 days=[{"date": req.start_date, "day_index": 0, "description": "旧的牡丹园餐馆描述",
                        "transportation": "公共交通", "accommodation": "酒店",
                        "attractions": [{"poi_id": k, "name": "景点" + k, "address": "地址" + k,
                                         "location": {"longitude": 116.4 + index * .01, "latitude": 39.9},
                                         "visit_duration": 90, "description": "参观"} for index, k in enumerate(keys)]}],
                 overall_suggestions="旧的牡丹园附近用餐")
    day = p.days[0]
    day.routes = [RoutePlan(
        origin=POIInfo(id=a.poi_id, name=a.name, location=a.location),
        destination=POIInfo(id=b.poi_id, name=b.name, location=b.location),
        mode="transit", duration_s=600, walking_distance_m=500,
    ) for a, b in zip(day.attractions, day.attractions[1:])]
    return req, p


class DiningTests(unittest.IsolatedAsyncioTestCase):
    async def test_split_query_keeps_local_cuisine_and_explicit_unverified_taste(self):
        maps = AmapService()
        async def call(name, args):
            pois = [] if args["keywords"] != "川菜 餐馆" else [{"id": "restaurant", "name": "川菜候选", "type": "四川菜(川菜)", "address": "地址", "location": args["location"]}]
            return CallToolResult(content=[], structuredContent={"pois": pois})
        maps._call_tool = AsyncMock(side_effect=call)
        req, plan = inputs()
        req.city = plan.city = "成都"
        req.dining_preference = "本地菜 清淡"
        state = DiningState()
        await DiningService(maps).finalize(plan, req, state)
        self.assertEqual(len(plan.days[0].meals), 2)
        self.assertTrue(all(x.search_relaxed for x in plan.dining_diagnostics))
        self.assertTrue(any("偏好适配尚未核实" in w for w in plan.days[0].dining_warnings))
        self.assertEqual(req.dining_preference, "本地菜 清淡")
        _, again = inputs()
        again.city = "成都"
        await DiningService(maps).finalize(again, req, state)
        self.assertEqual(maps._call_tool.await_count, 4)
        self.assertTrue(any("偏好适配尚未核实" in w for w in again.days[0].dining_warnings))

    async def test_strict_diet_and_unknown_preferences_are_not_broadened(self):
        for preference, extra in [("清真", ""), ("素食", ""), ("面食", ""), ("清淡", "必须清淡且有过敏限制")]:
            with self.subTest(preference=preference):
                maps = self.maps([])
                req, plan = inputs()
                req.dining_preference, req.free_text_input = preference, extra
                await DiningService(maps).finalize(plan, req, DiningState())
                self.assertEqual(maps._call_tool.await_count, 2)
                self.assertFalse(any(x.search_relaxed for x in plan.dining_diagnostics))
                self.assertEqual(plan.days[0].meals, [])

    async def test_three_final_days_with_missing_search_coordinates_keep_detail_budget(self):
        maps = AmapService()
        req, plan = inputs()
        req.max_leg_walking_m = 1000
        plan.days[0].routes = []
        state = DiningState()
        second = plan.days[0].model_copy(deep=True)
        second.day_index, second.date = 1, "2026-11-06"
        for index, a in enumerate(second.attractions):
            a.poi_id = "CD"[index]
            a.location.longitude += .1
        plan.days.append(second)
        third = second.model_copy(deep=True)
        third.day_index, third.date = 2, "2026-11-07"
        for index, a in enumerate(third.attractions):
            a.poi_id = "EF"[index]
            a.location.longitude += .1
        plan.days.append(third)
        known = {}
        async def call(name, args):
            if name == "maps_around_search":
                lon, lat = map(float, args["location"].split(","))
                pois = []
                for i in range(2):
                    key = f"meal-{lon}-{i}"
                    known[key] = {"id": key, "name": key, "address": key,
                                  "location": f"{lon+.001*(i+1)},{lat}"}
                    pois.append({"id": key, "name": key, "address": key})
                return CallToolResult(content=[], structuredContent={"pois": pois})
            return CallToolResult(content=[], structuredContent=known[args["id"]])
        maps._call_tool = AsyncMock(side_effect=call)
        for day in plan.days:
            a, b = day.attractions
            day.routes = [RoutePlan(origin=POIInfo(id=a.poi_id, name=a.name, location=a.location),
                                    destination=POIInfo(id=b.poi_id, name=b.name, location=b.location),
                                    mode="transit", duration_s=600, walking_distance_m=500)]
        await DiningService(maps).finalize(plan, req, state)
        self.assertEqual([len(day.meals) for day in plan.days], [2, 2, 2])
        self.assertEqual((state.searches, state.details), (6, 12))

    async def test_unsettled_routes_get_provisional_meals_then_reuse_them_when_routes_ready(self):
        maps = self.maps()
        req, plan = inputs()
        req.max_leg_walking_m = 1000
        plan.days[0].routes = []
        state = DiningState()
        await DiningService(maps).finalize(plan, req, state)
        self.assertEqual(maps._call_tool.await_count, 2)
        self.assertEqual((state.searches, state.details), (2, 0))
        self.assertEqual(len(plan.days[0].meals), 2)
        self.assertTrue(plan.days[0].dining_resolution.based_on_provisional_itinerary)
        self.assertTrue(all(x.status == "selected" for x in plan.dining_diagnostics))
        a, b = plan.days[0].attractions
        plan.days[0].routes = [RoutePlan(
            origin=POIInfo(id=a.poi_id, name=a.name, location=a.location),
            destination=POIInfo(id=b.poi_id, name=b.name, location=b.location),
            mode="transit", duration_s=600, walking_distance_m=500,
        )]
        await DiningService(maps).finalize(plan, req, state)
        self.assertEqual(maps._call_tool.await_count, 2)
        self.assertEqual(len(plan.days[0].meals), 2)
        self.assertFalse(plan.days[0].dining_resolution.based_on_provisional_itinerary)
        self.assertFalse(any("暂定景点" in m.description for m in plan.days[0].meals))
        self.assertTrue(all(x.status == "selected" for x in plan.dining_diagnostics if x.generation == 2))

    def maps(self, options=None):
        maps = AmapService()
        async def call(name, args):
            self.assertEqual(name, "maps_around_search")
            lon, lat = map(float, args["location"].split(","))
            pois = options if options is not None else [
                {"id": "near" + str(lon), "name": "就近候选", "address": "真实地址", "location": f"{lon+.001},{lat}"},
                {"id": "far", "name": "远处候选", "address": "远处地址", "location": "117.4,39.9"},
            ]
            return CallToolResult(content=[], structuredContent={"pois": pois})
        maps._call_tool = AsyncMock(side_effect=call)
        return maps

    async def test_selects_by_real_coordinates_and_updates_both_descriptions(self):
        maps = self.maps()
        req, plan = inputs()
        await DiningService(maps).finalize(plan, req, DiningState())
        self.assertEqual(len(plan.days[0].meals), 2)
        self.assertTrue(all(m.straight_line_distance_m < 200 for m in plan.days[0].meals))
        self.assertEqual([m.anchor_poi_id for m in plan.days[0].meals], ["A", "B"])
        self.assertNotIn("牡丹园", plan.days[0].description + plan.overall_suggestions)
        self.assertIn("不是步行距离", plan.days[0].description)
        self.assertIn("休息", plan.days[0].description)
        for call in maps._call_tool.await_args_list:
            self.assertEqual(call.args[1]["keywords"], "清淡 餐馆")

    async def test_far_results_do_not_become_nearby_meals(self):
        maps = self.maps([{"id": "far", "name": "远处", "location": "117.4,39.9"}])
        req, plan = inputs()
        await DiningService(maps).finalize(plan, req, DiningState())
        self.assertEqual(plan.days[0].meals, [])
        self.assertEqual([x.status for x in plan.dining_diagnostics], ["no_nearby_candidate"] * 2)
        self.assertEqual(len(plan.days[0].dining_resolution.unresolved), 2)

    async def test_missing_coordinates_do_not_become_zero_distance(self):
        maps = self.maps([{"id": "unknown", "name": "坐标缺失"}])
        maps.get_poi_detail = AsyncMock(side_effect=AmapServiceError("private url and key"))
        req, plan = inputs()
        await DiningService(maps).finalize(plan, req, DiningState())
        self.assertEqual(plan.days[0].meals, [])
        self.assertNotIn("private", plan.model_dump_json())

    async def test_same_anchors_reuse_searches_and_changed_anchors_query_again(self):
        maps = self.maps()
        req, first = inputs()
        state = DiningState()
        await DiningService(maps).finalize(first, req, state)
        _, second = inputs()
        await DiningService(maps).finalize(second, req, state)
        self.assertEqual(maps._call_tool.await_count, 2)
        self.assertTrue(all(x.cache_hit for x in second.dining_diagnostics if x.generation == 2))
        self.assertEqual(len(first.dining_diagnostics), 2)
        _, third = inputs("CD")
        await DiningService(maps).finalize(third, req, state)
        self.assertEqual(maps._call_tool.await_count, 4)
        self.assertEqual([m.anchor_poi_id for m in third.days[0].meals], ["C", "D"])

    async def test_request_budget_stops_extra_searches(self):
        maps = self.maps()
        req, plan = inputs()
        with patch("app.services.dining_service.MAX_SEARCHES_PER_REQUEST", 1):
            await DiningService(maps).finalize(plan, req, DiningState())
        self.assertEqual(maps._call_tool.await_count, 1)
        self.assertEqual(plan.dining_diagnostics[-1].status, "budget_exhausted")
        self.assertEqual(len(plan.days[0].meals), 1)

    async def test_provider_error_is_visible_and_is_not_cached_as_no_restaurant(self):
        maps = self.maps()
        req, plan = inputs()
        state = DiningState()
        maps._call_tool.side_effect = AmapServiceError("private")
        await DiningService(maps).finalize(plan, req, state)
        self.assertTrue(all(x.status == "provider_error" for x in plan.dining_diagnostics))
        self.assertEqual(state.cache, {})
        self.assertNotIn("private", plan.model_dump_json())


if __name__ == "__main__":
    unittest.main()
