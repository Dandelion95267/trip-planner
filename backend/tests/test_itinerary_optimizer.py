"""顺序优化的纯计算测试。"""

import unittest

from app.models.schemas import Location
from app.services.itinerary_optimizer import optimize_visit_order


def points(longitudes):
    return [Location(longitude=value, latitude=39.9) for value in longitudes]


class OrderOptimizerTests(unittest.TestCase):
    def test_reduces_backtracking_without_moving_start_or_dropping_points(self):
        locations = points([116.4, 116.42, 116.41, 116.43])
        order, before, after = optimize_visit_order(locations)
        self.assertEqual(order, [0, 2, 1, 3])
        self.assertEqual(sorted(order), list(range(len(locations))))
        self.assertLess(after, before)
        self.assertGreater(before, 1000)
        self.assertEqual([p.longitude for p in locations], [116.4, 116.42, 116.41, 116.43])

    def test_already_short_order_is_preserved(self):
        order, before, after = optimize_visit_order(points([116.4, 116.41, 116.42, 116.43]))
        self.assertEqual(order, [0, 1, 2, 3])
        self.assertEqual(before, after)

    def test_empty_single_and_two_points(self):
        for values in [[], [116.4], [116.4, 116.41]]:
            with self.subTest(values=values):
                order, before, after = optimize_visit_order(points(values))
                self.assertEqual(order, list(range(len(values))))
                self.assertEqual(before, after)

    def test_identical_coordinates_do_not_trigger_unnecessary_reordering(self):
        order, before, after = optimize_visit_order(points([116.4, 116.4, 116.4]))
        self.assertEqual(order, [0, 1, 2])
        self.assertEqual(before, 0)
        self.assertEqual(after, 0)


if __name__ == "__main__":
    unittest.main()
