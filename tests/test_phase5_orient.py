"""Phase 5 strict selftest: Weg A — direkte Orientierung (5a .pts-Winkel, 5b PNG-Rotation)."""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import cv2

from panolevel.buildpts import set_orientation
from panolevel.detect import detect_vertical_report
from panolevel.orient import (
    estimate_zenith,
    roll_pitch_from_zenith,
    rotate_equirect,
    zenith_to_rotation,
)
from panolevel.ptsio import load_pts
from panolevel.synthetic import write_roll_fixture

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PTS = ROOT / "tests" / "fixtures" / "2026-06-12_13.44.31 Panorama.pts"


class OrientTests(unittest.TestCase):
    def test_estimates_known_roll(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for truth in (7.0, -5.0, 12.0):
                f = write_roll_fixture(Path(tmp) / f"r{truth}.png", roll_deg=truth)
                rep = detect_vertical_report(f, reduce=1)
                z, _ = estimate_zenith(rep.lines, *rep.image_size)
                roll, pitch = roll_pitch_from_zenith(z)
                self.assertLess(abs(roll - truth), 1.0, f"roll {roll} vs {truth}")
                self.assertLess(abs(pitch), 1.0, f"pitch {pitch}")

    def test_rotation_levels_a_tilted_pano(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = write_roll_fixture(Path(tmp) / "t.png", roll_deg=9.0)
            img = cv2.imread(str(f))
            rep = detect_vertical_report(f, reduce=1)
            z, _ = estimate_zenith(rep.lines, *rep.image_size)
            rot = rotate_equirect(img, zenith_to_rotation(z))
            self.assertEqual(img.shape, rot.shape)
            out = Path(tmp) / "rot.png"
            cv2.imwrite(str(out), rot)
            rep2 = detect_vertical_report(out, reduce=1)
            self.assertFalse(rep2.uncertain, rep2.to_json())
            self.assertIsNotNone(rep2.median_angle_deg)
            self.assertLess(abs(rep2.median_angle_deg), 1.0)


class SetOrientationTests(unittest.TestCase):
    def test_writes_params_and_freezes_optimizer_keeps_yaw(self) -> None:
        doc = load_pts(TEMPLATE_PTS)
        yaw_before = doc["project"]["imagegroups"][0]["position"]["params"]["yaw"]
        set_orientation(doc, roll=-7.0, pitch=0.5)
        pos = doc["project"]["imagegroups"][0]["position"]
        self.assertAlmostEqual(-7.0, pos["params"]["roll"])
        self.assertAlmostEqual(0.5, pos["params"]["pitch"])
        self.assertEqual(yaw_before, pos["params"]["yaw"])
        self.assertFalse(pos["optimizerflags"]["pitch"])
        self.assertFalse(pos["optimizerflags"]["roll"])


def _run_autolevel(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "panolevel", "autolevel", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


class ModeCliTests(unittest.TestCase):
    def test_rotate_mode_straightens_png_without_pts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            f = write_roll_fixture(tmp / "t.png", width=2048, height=1024, roll_deg=8.0)
            proc = _run_autolevel("--image", str(f), "--mode", "rotate", "--reduce", "1")
            self.assertEqual(0, proc.returncode, proc.stderr)
            rep = json.loads(proc.stdout)
            self.assertEqual("rotate", rep["mode"])
            out_png = tmp / "t.autolevel.png"
            self.assertEqual(str(out_png), rep["outputs"]["image"])
            self.assertTrue(out_png.exists())
            rep2 = detect_vertical_report(out_png, reduce=1)
            self.assertFalse(rep2.uncertain, rep2.to_json())
            self.assertLess(abs(rep2.median_angle_deg), 1.5)

    def test_angle_mode_writes_orientation_into_pts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            f = write_roll_fixture(tmp / "a.png", width=2048, height=1024, roll_deg=7.0)
            sidecar = load_pts(TEMPLATE_PTS)
            sidecar["project"]["imagegroups"][0]["size"] = [2048, 1024]
            (tmp / "a.pts").write_text(json.dumps(sidecar), encoding="utf-8")
            proc = _run_autolevel(
                "--image",
                str(f),
                "--pts",
                str(tmp / "a.pts"),
                "--mode",
                "angle",
                "--reduce",
                "1",
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            doc = load_pts(tmp / "a.autolevel.pts")
            pos = doc["project"]["imagegroups"][0]["position"]
            self.assertNotEqual(0.0, pos["params"]["roll"])
            self.assertFalse(pos["optimizerflags"]["pitch"])
            self.assertFalse(pos["optimizerflags"]["roll"])

    def test_angle_mode_without_pts_or_template_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = write_roll_fixture(Path(tmp) / "a.png", width=2048, height=1024)
            proc = _run_autolevel("--image", str(f), "--mode", "angle", "--reduce", "1")
            self.assertNotEqual(0, proc.returncode)


TASKS = {
    "orient": OrientTests,
    "setorient": SetOrientationTests,
    "cli": ModeCliTests,
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
