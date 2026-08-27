import argparse
import unittest

from pdf2dxf.cad.color import rgb_to_true_color
from pdf2dxf.cli import parse_pages
from pdf2dxf.geometry import Point, cubic_bezier


class CoreTests(unittest.TestCase):
    def test_page_ranges_are_one_based_and_deduplicated(self):
        self.assertEqual(parse_pages("1,3-5,4"), [0, 2, 3, 4])

    def test_invalid_page_range_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_pages("5-3")

    def test_curve_ends_are_preserved(self):
        segments = cubic_bezier(
            Point(0, 0), Point(0, 1), Point(1, 1), Point(1, 0), 8
        )
        self.assertEqual(len(segments), 8)
        self.assertEqual(segments[0].start, Point(0, 0))
        self.assertEqual(segments[-1].end, Point(1, 0))

    def test_true_color(self):
        self.assertEqual(rgb_to_true_color((1.0, 0.5, 0.0)), 0xFF8000)

if __name__ == "__main__":
    unittest.main()
