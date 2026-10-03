from __future__ import annotations

import argparse
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from panolevel.detect import detect_vertical_report
from panolevel.synthetic import write_roll_fixture, write_texture_fixture

ROOT = Path(__file__).resolve().parents[1]
# Real 16384x8192 test panorama (not in the repo); tests needing it are skipped without it.
REAL_IMAGE = Path(os.environ.get("PANOLEVEL_REAL_PANO", "/nonexistent"))


class RealImageMixin(unittest.TestCase):
    def assert_real_image_available(self) -> None:
        if not REAL_IMAGE.is_file():
            self.skipTest("PANOLEVEL_REAL_PANO not set or missing")


class DetectTests(RealImageMixin):
    def test_detects_full_resolution_vertical_lines(self) -> None:
        self.assert_real_image_available()
        report = detect_vertical_report(REAL_IMAGE)

        self.assertFalse(report.uncertain, report.to_json())
        self.assertGreaterEqual(len(report.lines), 6)
        self.assertLessEqual(len(report.lines), 14)
        self.assertEqual((16384, 8192), report.image_size)
        self.assertEqual((4096, 2048), report.reduced_size)
        self.assertEqual((4.0, 4.0), report.scale)

        width, height = report.image_size
        for line in report.lines:
            (x1, y1), (x2, y2) = line
            self.assertGreaterEqual(x1, 0)
            self.assertLess(x1, width)
            self.assertGreaterEqual(x2, 0)
            self.assertLess(x2, width)
            self.assertGreaterEqual(y1, 0)
            self.assertLess(y1, height)
            self.assertGreaterEqual(y2, 0)
            self.assertLess(y2, height)
            angle = math.degrees(math.atan2(abs(x2 - x1), abs(y2 - y1)))
            self.assertLessEqual(angle, 20.5)


class SpreadTests(RealImageMixin):
    def test_reduced4_adaptive_spreads_better_than_naive_reduced8(self) -> None:
        self.assert_real_image_available()
        naive = detect_vertical_report(
            REAL_IMAGE,
            reduce=8,
            adaptive=False,
            target_columns=12,
            max_lines=12,
        )
        improved = detect_vertical_report(REAL_IMAGE)

        self.assertGreaterEqual(len(improved.lines), len(naive.lines))
        self.assertGreater(improved.column_coverage, naive.column_coverage)
        self.assertGreater(improved.middle_coverage, naive.middle_coverage)
        self.assertGreaterEqual(len(improved.occupied_columns), 8)
        self.assertGreaterEqual(improved.middle_coverage, 2)
        self.assertFalse(improved.uncertain, improved.to_json())


class RobustTests(unittest.TestCase):
    def test_roll_signature_has_stable_sign(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pos = write_roll_fixture(Path(tmp) / "roll_pos.png", roll_deg=7.0)
            neg = write_roll_fixture(Path(tmp) / "roll_neg.png", roll_deg=-7.0)

            pos_report = detect_vertical_report(pos, reduce=1)
            neg_report = detect_vertical_report(neg, reduce=1)

            self.assertFalse(pos_report.uncertain, pos_report.to_json())
            self.assertFalse(neg_report.uncertain, neg_report.to_json())
            self.assertIsNotNone(pos_report.median_angle_deg)
            self.assertIsNotNone(neg_report.median_angle_deg)
            self.assertLess(pos_report.median_angle_deg, 0.0)
            self.assertGreater(neg_report.median_angle_deg, 0.0)

    def test_oblique_outlier_is_rejected_by_consistency_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_roll_fixture(Path(tmp) / "roll_outlier.png", outlier=True)
            report = detect_vertical_report(path, reduce=1)

            self.assertFalse(report.uncertain, report.to_json())
            self.assertGreaterEqual(report.outlier_count, 1, report.to_json())
            self.assertGreaterEqual(len(report.lines), 6)

    def test_texture_without_consistent_verticals_is_uncertain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_texture_fixture(Path(tmp) / "texture.png")
            report = detect_vertical_report(path, reduce=1)

            self.assertTrue(report.uncertain, report.to_json())
            self.assertLessEqual(len(report.lines), 2)
            self.assertLess(report.confidence, 0.42)


class CliTests(RealImageMixin):
    def test_detect_cli_outputs_json_report_and_parameters_affect_band(self) -> None:
        self.assert_real_image_available()
        base = _run_detect_cli(
            "--image",
            str(REAL_IMAGE),
            "--reduce",
            "4",
            "--max-lines",
            "12",
        )
        shifted = _run_detect_cli(
            "--image",
            str(REAL_IMAGE),
            "--band-center",
            "0.75",
            "--band-height",
            "0.20",
            "--reduce",
            "4",
            "--max-lines",
            "12",
        )

        self.assertIn("lines", base)
        self.assertIn("confidence", base)
        self.assertIn("uncertain", base)
        self.assertGreaterEqual(len(base["lines"]), 6)
        self.assertNotEqual(base["band"], shifted["band"])
        self.assertNotEqual(base["lines"], shifted["lines"])


def _run_detect_cli(*args: str) -> dict[str, object]:
    proc = subprocess.run(
        [sys.executable, "-m", "panolevel", "detect", *args],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr)
    return json.loads(proc.stdout)


TASKS = {
    "cli": CliTests,
    "detect": DetectTests,
    "robust": RobustTests,
    "spread": SpreadTests,
}


def build_suite(task: str | None) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    selected = [TASKS[task]] if task else TASKS.values()
    for case in selected:
        suite.addTests(loader.loadTestsFromTestCase(case))
    return suite


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--task", choices=sorted(TASKS))
    args = parser.parse_args(argv)

    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(build_suite(args.task))
    failures = [
        {"test": str(test), "traceback": traceback}
        for test, traceback in result.failures + result.errors
    ]
    report = {
        "task": args.task or "all",
        "tests_run": result.testsRun,
        "failures": failures,
        "strict_ready": result.wasSuccessful(),
        "runner_output": stream.getvalue().strip().splitlines(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and not result.wasSuccessful():
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
