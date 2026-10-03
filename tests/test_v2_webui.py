"""Phase v2 strict selftest: yaw rotation for web UI."""

from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

from panolevel.orient import combined_rotation, yaw_rotation, zenith_to_rotation
from panolevel.ptsio import load_pts as _load_pts
from panolevel.server import create_app
from panolevel.synthetic import write_roll_fixture


class ServerEstimateTests(unittest.TestCase):
    def _client(self, tmp):
        img = write_roll_fixture(Path(tmp) / "p.png", width=2048, height=1024, roll_deg=8.0)
        return TestClient(create_app(img, preview_reduce=2, detect_kwargs={"reduce": 1})), img

    def test_detect_returns_lines_and_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, _ = self._client(tmp)
            r = client.post("/detect")
            self.assertEqual(200, r.status_code)
            d = r.json()
            self.assertTrue(d["ok"])
            self.assertGreaterEqual(len(d["lines"]), 6)
            self.assertTrue(d["preview"].startswith("data:image/png;base64,"))
            self.assertEqual(1024, d["width"])
            self.assertEqual(512, d["height"])

    def test_estimate_returns_roll_near_truth(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, _ = self._client(tmp)
            lines = client.post("/detect").json()["lines"]
            r = client.post("/estimate", json={"lines": lines, "yaw": 0.0})
            d = r.json()
            self.assertTrue(d["ok"])
            self.assertLess(abs(d["roll"] - 8.0), 1.5)
            self.assertTrue(d["preview"].startswith("data:image/png;base64,"))

    def test_estimate_too_few_lines_is_ok_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, _ = self._client(tmp)
            r = client.post("/estimate", json={"lines": [[1, 2, 3, 4]], "yaw": 0.0})
            self.assertFalse(r.json()["ok"])

    def test_estimate_malformed_lines_returns_ok_false_not_500(self):
        """Fix 1: lines with <4 elements must return ok:false, not HTTP 500."""
        with tempfile.TemporaryDirectory() as tmp:
            client, _ = self._client(tmp)
            # two lines each with only 2 elements — would cause IndexError at l[2]
            r = client.post("/estimate", json={"lines": [[1, 2], [3, 4]], "yaw": 0.0})
            self.assertEqual(200, r.status_code)
            self.assertFalse(r.json()["ok"])

    def test_estimate_mixed_valid_short_lines_ok_false(self):
        """Fix 1: one valid line + one short line → only 1 valid → ok:false."""
        with tempfile.TemporaryDirectory() as tmp:
            client, _ = self._client(tmp)
            r = client.post("/estimate", json={"lines": [[1, 2, 3, 4], [5, 6]], "yaw": 0.0})
            self.assertEqual(200, r.status_code)
            self.assertFalse(r.json()["ok"])


class OrientYawTests(unittest.TestCase):
    def test_yaw_zero_is_identity(self):
        self.assertTrue(np.allclose(yaw_rotation(0.0), np.eye(3), atol=1e-9))

    def test_yaw_90_turns_front_to_side(self):
        # front = (0,0,1); a +90 deg yaw about y maps it onto the x axis.
        out = yaw_rotation(90.0) @ np.array([0.0, 0.0, 1.0])
        self.assertAlmostEqual(abs(out[0]), 1.0, places=6)
        self.assertAlmostEqual(out[1], 0.0, places=6)
        self.assertAlmostEqual(out[2], 0.0, places=6)

    def test_yaw_preserves_vertical_axis(self):
        out = yaw_rotation(37.0) @ np.array([0.0, 1.0, 0.0])
        self.assertTrue(np.allclose(out, [0.0, 1.0, 0.0], atol=1e-9))

    def test_combined_zero_yaw_equals_zenith_rotation(self):
        z = np.array([0.12, 0.99, 0.0])
        z = z / np.linalg.norm(z)
        self.assertTrue(np.allclose(combined_rotation(z, 0.0), zenith_to_rotation(z), atol=1e-9))


class ServerExportTests(unittest.TestCase):
    def test_export_png_writes_full_res_rotated(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            img = write_roll_fixture(tmp / "p.png", width=2048, height=1024, roll_deg=8.0)
            client = TestClient(create_app(img, preview_reduce=2, detect_kwargs={"reduce": 1}))
            lines = client.post("/detect").json()["lines"]
            out = tmp / "straight.png"
            r = client.post(
                "/export", json={"lines": lines, "yaw": 0.0, "mode": "png", "out": str(out)}
            )
            d = r.json()
            self.assertTrue(d["ok"])
            self.assertEqual(str(out), d["output"])
            self.assertEqual((1024, 2048, 3), cv2.imread(str(out)).shape)  # full res

    def test_export_pts_writes_orientation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            img = write_roll_fixture(tmp / "p.png", width=2048, height=1024, roll_deg=8.0)
            sidecar = _load_pts(Path("tests/fixtures/2026-06-12_13.44.31 Panorama.pts"))
            sidecar["project"]["imagegroups"][0]["size"] = [2048, 1024]
            pts = tmp / "p.pts"
            pts.write_text(json.dumps(sidecar), encoding="utf-8")
            client = TestClient(
                create_app(img, pts=pts, preview_reduce=2, detect_kwargs={"reduce": 1})
            )
            lines = client.post("/detect").json()["lines"]
            out = tmp / "p.autolevel.pts"
            r = client.post(
                "/export", json={"lines": lines, "yaw": 15.0, "mode": "pts", "out": str(out)}
            )
            self.assertTrue(r.json()["ok"])
            pos = _load_pts(out)["project"]["imagegroups"][0]["position"]
            self.assertNotEqual(0.0, pos["params"]["roll"])
            self.assertAlmostEqual(15.0, pos["params"]["yaw"])
            self.assertFalse(pos["optimizerflags"]["roll"])

    def test_export_pts_without_pts_or_template_is_ok_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            img = write_roll_fixture(tmp / "p.png", width=2048, height=1024, roll_deg=8.0)
            client = TestClient(create_app(img, preview_reduce=2, detect_kwargs={"reduce": 1}))
            lines = client.post("/detect").json()["lines"]
            r = client.post("/export", json={"lines": lines, "yaw": 0.0, "mode": "pts"})
            self.assertFalse(r.json()["ok"])

    def test_export_png_out_outside_allowed_dirs_blocked(self):
        """Fix 2: out path outside image dir and cwd must return ok:false.

        The file must not be created.
        """
        with tempfile.TemporaryDirectory() as img_tmp, tempfile.TemporaryDirectory() as evil_tmp:
            img_tmp = Path(img_tmp)
            evil_tmp = Path(evil_tmp)
            img = write_roll_fixture(img_tmp / "p.png", width=2048, height=1024, roll_deg=8.0)
            client = TestClient(create_app(img, preview_reduce=2, detect_kwargs={"reduce": 1}))
            lines = client.post("/detect").json()["lines"]
            evil_out = evil_tmp / "evil.png"
            r = client.post(
                "/export", json={"lines": lines, "yaw": 0.0, "mode": "png", "out": str(evil_out)}
            )
            d = r.json()
            self.assertFalse(d["ok"])
            self.assertFalse(evil_out.exists(), "file must NOT be created outside allowed dirs")

    def test_export_png_out_inside_image_dir_allowed(self):
        """Fix 2 regression: out path inside image dir must still work (ok:true)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            img = write_roll_fixture(tmp / "p.png", width=2048, height=1024, roll_deg=8.0)
            client = TestClient(create_app(img, preview_reduce=2, detect_kwargs={"reduce": 1}))
            lines = client.post("/detect").json()["lines"]
            allowed_out = tmp / "allowed.png"
            r = client.post(
                "/export", json={"lines": lines, "yaw": 0.0, "mode": "png", "out": str(allowed_out)}
            )
            d = r.json()
            self.assertTrue(d["ok"])
            self.assertTrue(allowed_out.exists())


class IndexRouteTest(unittest.TestCase):
    def test_index_serves_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            img = write_roll_fixture(Path(tmp) / "p.png", width=2048, height=1024)
            client = TestClient(create_app(img, preview_reduce=2, detect_kwargs={"reduce": 1}))
            r = client.get("/")
            self.assertEqual(200, r.status_code)
            self.assertIn("<canvas", r.text)
            self.assertIn("app.js", r.text)


TASKS = {
    "estimate": ServerEstimateTests,
    "export": ServerExportTests,
    "index": IndexRouteTest,
    "yaw": OrientYawTests,
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
    parser.add_argument("--task", choices=sorted(TASKS.keys()))
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
