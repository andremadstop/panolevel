"""Phase 3 strict selftest: Confirm/Output + Projekt-Erzeugung + Settings.

Tests use the committed real ``.pts`` fixtures as deterministic templates and
``synthetic`` panoramas for overlay/E2E — never the 16384x8192 originals.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from panolevel.buildpts import OutputSettings, build_pts_from_template
from panolevel.overlay import render_overlay
from panolevel.ptsio import list_vertical_lines, load_pts
from panolevel.synthetic import write_roll_fixture

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PTS = ROOT / "tests" / "fixtures" / "2026-06-12_13.44.31 Panorama.pts"


def _ig(doc: dict) -> dict:
    return doc["project"]["imagegroups"][0]


class BuildPtsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.template = load_pts(TEMPLATE_PTS)

    def test_builds_loadable_single_image_project_from_template(self) -> None:
        lines = [((100, 200), (100, 800)), ((300, 210), (305, 790))]
        doc = build_pts_from_template(
            self.template,
            filename="render.png",
            width=4000,
            height=2000,
            lines=lines,
        )
        ig = _ig(doc)
        self.assertEqual([4000, 2000], ig["size"])
        self.assertEqual("render.png", ig["images"][0]["filename"])
        params = ig["position"]["params"]
        self.assertEqual(0.0, params["yaw"])
        self.assertEqual(0.0, params["pitch"])
        self.assertEqual(0.0, params["roll"])
        self.assertTrue(ig["position"]["optimizerflags"]["pitch"])
        self.assertTrue(ig["position"]["optimizerflags"]["roll"])
        cps = doc["project"]["controlpoints"]
        self.assertEqual(len(lines), len(cps))
        self.assertTrue(all(cp.get("t") == 1 for cp in cps))
        self.assertEqual(len(lines), len(list_vertical_lines(doc)))

    def test_output_settings_are_explicit_not_inherited(self) -> None:
        # Template ships fileformat=tiff datatype=u8; default must force u16.
        self.assertEqual("u8", self.template["project"]["panoramaparams"]["tiffparams"]["datatype"])
        doc = build_pts_from_template(self.template, filename="r.png", width=10, height=5, lines=[])
        pp = doc["project"]["panoramaparams"]
        self.assertEqual("tiff", pp["fileformat"])
        self.assertEqual("u16", pp["tiffparams"]["datatype"])

    def test_output_settings_override(self) -> None:
        doc = build_pts_from_template(
            self.template,
            filename="r.png",
            width=10,
            height=5,
            lines=[],
            output=OutputSettings(fileformat="jpeg"),
        )
        self.assertEqual("jpeg", doc["project"]["panoramaparams"]["fileformat"])

    def test_template_is_not_mutated(self) -> None:
        before = hashlib.sha256(json.dumps(self.template, sort_keys=True).encode()).hexdigest()
        build_pts_from_template(
            self.template,
            filename="r.png",
            width=10,
            height=5,
            lines=[((1, 2), (1, 4))],
        )
        after = hashlib.sha256(json.dumps(self.template, sort_keys=True).encode()).hexdigest()
        self.assertEqual(before, after)

    def test_optional_metadata_stamped_when_provided(self) -> None:
        doc = build_pts_from_template(
            self.template,
            filename="r.png",
            width=10,
            height=5,
            lines=[],
            filesize=12345,
            filetimestamp=1700000000,
        )
        meta = _ig(doc)["images"][0]["metadata"]
        self.assertEqual(12345, meta["filesize"])
        self.assertEqual(1700000000, meta["filetimestamp"])


class OverlayTests(unittest.TestCase):
    def test_overlay_png_shows_lines_and_search_band(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            img = write_roll_fixture(Path(tmp) / "pano.png", width=2048, height=1024)
            out = Path(tmp) / "overlay.png"
            lines = [((400, 300), (405, 720)), ((1200, 310), (1198, 710))]
            band = (300, 720)
            result = render_overlay(img, lines, band, out, reduce=2)

            self.assertEqual(out, result)
            self.assertTrue(out.exists())
            rendered = cv2.imread(str(out))
            self.assertIsNotNone(rendered)
            # reduce=2 -> half size of the 2048x1024 source
            self.assertEqual((512, 1024), (rendered.shape[0], rendered.shape[1]))
            # Lines drawn in red (BGR), band drawn in green — both must be present.
            self.assertTrue(_has_color(rendered, (0, 0, 255)), "no line pixels")
            self.assertTrue(_has_color(rendered, (0, 255, 0)), "no band pixels")

    def test_overlay_band_position_scales_with_reduce(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            img = write_roll_fixture(Path(tmp) / "pano.png", width=2048, height=1024)
            out = Path(tmp) / "overlay.png"
            band = (400, 600)  # full-res rows
            render_overlay(img, [], band, out, reduce=2)
            rendered = cv2.imread(str(out))
            green_rows = np.where((rendered == (0, 255, 0)).all(axis=2).any(axis=1))[0]
            self.assertTrue(len(green_rows) >= 2)
            # band rows scale by 1/reduce -> ~200 and ~300
            self.assertTrue(any(abs(r - 200) <= 2 for r in green_rows))
            self.assertTrue(any(abs(r - 300) <= 2 for r in green_rows))


def _has_color(image: np.ndarray, bgr: tuple[int, int, int]) -> bool:
    return bool((image == bgr).all(axis=2).any())


def _run_autolevel(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "panolevel", "autolevel", *args],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AutolevelCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.template = load_pts(TEMPLATE_PTS)

    def test_existing_pts_injects_into_copy_leaving_original_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            img = write_roll_fixture(tmp / "pano.png", width=2048, height=1024)
            pts = tmp / "pano.pts"
            sidecar = build_pts_from_template(
                self.template, filename="pano.png", width=2048, height=1024, lines=[]
            )
            pts.write_text(json.dumps(sidecar), encoding="utf-8")
            before = _sha(pts)

            proc = _run_autolevel("--image", str(img), "--pts", str(pts), "--reduce", "1")
            self.assertEqual(0, proc.returncode, proc.stderr)
            report = json.loads(proc.stdout)

            out_pts = tmp / "pano.autolevel.pts"
            overlay = Path(report["outputs"]["overlay"])
            self.assertTrue(out_pts.exists())
            self.assertTrue(overlay.exists())
            self.assertEqual(str(out_pts), report["outputs"]["pts"])
            self.assertIn("confidence", report)
            self.assertIn("uncertain", report)
            self.assertGreaterEqual(report["lines"], 6)
            self.assertEqual(before, _sha(pts), "original .pts was modified")

            doc = load_pts(out_pts)
            self.assertEqual(report["lines"], len(list_vertical_lines(doc)))
            flags = doc["project"]["imagegroups"][0]["position"]["optimizerflags"]
            self.assertTrue(flags["pitch"])
            self.assertTrue(flags["roll"])

    def test_existing_pts_preserves_prior_controlpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            img = write_roll_fixture(tmp / "pano.png", width=2048, height=1024)
            pts = tmp / "pano.pts"
            # Template fixture ships 3 prior control points + 4 in the other one;
            # use it verbatim so the prior CPs are real, only resize to the image.
            sidecar = copy.deepcopy(self.template)
            sidecar["project"]["imagegroups"][0]["size"] = [2048, 1024]
            prior_cps = sidecar["project"]["controlpoints"]
            self.assertGreaterEqual(len(prior_cps), 1)
            pts.write_text(json.dumps(sidecar), encoding="utf-8")

            proc = _run_autolevel("--image", str(img), "--pts", str(pts), "--reduce", "1")
            self.assertEqual(0, proc.returncode, proc.stderr)

            doc = load_pts(tmp / "pano.autolevel.pts")
            result_cps = doc["project"]["controlpoints"]
            for cp in prior_cps:
                self.assertIn(cp, result_cps, "prior control point was dropped")
            # Auto verticals added on top of the preserved prior CPs.
            self.assertGreater(len(result_cps), len(prior_cps))

    def test_no_pts_builds_from_template_with_default_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            img = write_roll_fixture(tmp / "scene.png", width=2048, height=1024)

            proc = _run_autolevel(
                "--image", str(img), "--template", str(TEMPLATE_PTS), "--reduce", "1"
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            report = json.loads(proc.stdout)

            out_pts = tmp / "scene.autolevel.pts"
            self.assertTrue(out_pts.exists())
            self.assertTrue(Path(report["outputs"]["overlay"]).exists())
            doc = load_pts(out_pts)
            ig = doc["project"]["imagegroups"][0]
            self.assertEqual([2048, 1024], ig["size"])
            self.assertEqual("scene.png", ig["images"][0]["filename"])
            self.assertEqual("u16", doc["project"]["panoramaparams"]["tiffparams"]["datatype"])

    def test_missing_pts_and_template_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            img = write_roll_fixture(tmp / "lonely.png", width=2048, height=1024)
            proc = _run_autolevel("--image", str(img), "--reduce", "1")
            self.assertNotEqual(0, proc.returncode)
            self.assertIn("template", proc.stderr.lower())


TASKS = {
    "autolevel": AutolevelCliTests,
    "buildpts": BuildPtsTests,
    "overlay": OverlayTests,
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
