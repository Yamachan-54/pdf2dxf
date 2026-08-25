import argparse
from pathlib import Path
import tempfile
import unittest

from pdf2dxf.cli import parse_pages
from pdf2dxf.dxf import DxfLine, rgb_to_true_color, write_ascii_dxf
from pdf2dxf.geometry import Point, Segment, cubic_bezier


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

    def test_writes_minimal_dxf(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "drawing.dxf"
            write_ascii_dxf(
                target,
                [DxfLine(Segment(Point(1, 2), Point(3, 4)), "PDF_PAGE_1")],
                4,
            )
            text = target.read_text(encoding="ascii")
            self.assertIn("$INSUNITS\n70\n4", text)
            self.assertIn("LINE\n100\nAcDbEntity", text)
            self.assertTrue(text.endswith("0\nEOF\n"))


if __name__ == "__main__":
    unittest.main()
