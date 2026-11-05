"""多天路线预算先覆盖原路段，再分配修复时间，不访问外部服务。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.agents.trip_planner_agent import TripPlannerAgent
from app.services.amap_service import AmapRouteNotFoundError
from app.services.route_search import RouteSearchState
from test_walking_limits import plan, place, request, route


class RouteBudgetTests(unittest.IsolatedAsyncioTestCase):
    def inputs(self):
        current = plan()
        second = plan(("C", "D")).days[0]
        second.day_index, second.date = 1, "2026-11-06"
        current.days.append(second)
        current.end_date = "2026-11-06"
        req = request(travel_days=2, end_date=current.end_date, max_leg_walking_m=None)
        return req, current

    async def test_all_original_days_are_queried_before_replacements(self):
        calls = []
        async def lookup(q):
            pair = q.origin_poi_id, q.destination_poi_id
            calls.append(pair)
            if pair == ("C", "D"):
                return route(*pair)
            raise AmapRouteNotFoundError("none")
        maps = Mock(plan_route=AsyncMock(side_effect=lookup))
        req, current = self.inputs()
        await TripPlannerAgent(llm=Mock(), amap_service=maps)._attach_routes(current, req, [place(k) for k in "ABCDE"])
        self.assertEqual(calls[:2], [("A", "B"), ("C", "D")])
        self.assertEqual(len(current.days[1].routes), 1)

    async def test_slow_first_day_cannot_consume_second_days_original_query(self):
        calls = []
        clock = SimpleNamespace(now=0.0, allocation=0.0)
        @asynccontextmanager
        async def timeout(seconds):
            clock.allocation = seconds
            yield
        async def lookup(q):
            pair = q.origin_poi_id, q.destination_poi_id
            calls.append(pair)
            if pair == ("C", "D"):
                return route(*pair)
            # 让首日消耗其获分配的全部时间，不依赖Windows的80毫秒调度精度。
            clock.now += clock.allocation
            raise TimeoutError()
        maps = Mock(plan_route=AsyncMock(side_effect=lookup))
        req, current = self.inputs()
        with patch("app.agents.trip_planner_agent.ROUTE_BUDGET_SECONDS", 60), \
             patch("app.agents.trip_planner_agent.asyncio.get_running_loop", return_value=SimpleNamespace(time=lambda: clock.now)), \
             patch("app.agents.trip_planner_agent.asyncio.timeout", timeout):
            await TripPlannerAgent(llm=Mock(), amap_service=maps)._attach_routes(current, req, [place(k) for k in "ABCDE"])
        self.assertIn(("C", "D"), calls)
        self.assertEqual(len(current.days[1].routes), 1)
        self.assertEqual(current.days[1].routes[0].origin.id, "C")

    async def test_unlocked_second_original_can_start_a_valid_replacement(self):
        calls = []
        async def lookup(q):
            pair = q.origin_poi_id, q.destination_poi_id
            calls.append(pair)
            if pair == ("B", "G"):
                return route(*pair)
            raise AmapRouteNotFoundError("none")
        maps = Mock(plan_route=AsyncMock(side_effect=lookup))
        planner = TripPlannerAgent(llm=Mock(), amap_service=maps)
        state = RouteSearchState()
        for _ in range(2):
            current = plan()
            await planner._attach_routes(
                current, request(max_leg_walking_m=None), [place(k) for k in "ABCDEFGH"], state,
            )
        self.assertEqual([a.poi_id for a in current.days[0].attractions], ["B", "G"])
        self.assertLessEqual(len(calls), 26)
