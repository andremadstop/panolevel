"""Phase 4 strict selftest: Batch — folder run, pairing, robust collective report."""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from panolevel.batch import pair_panos, run_batch
from panolevel.ptsio import load_pts
from panolevel.synthetic import write_roll_fixture

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PTS = ROOT / "tests" / "fixtures" / "2026-06-12_13.44.31 Panorama.pts"


def _sized_template(width: int, height: int) -> dict:
    doc = load_pts(TEMPLATE_PTS)
    doc["project"]["imagegroups"][0]["size"] = [width, height]
    return doc


class PairTests(unittest.TestCase):
    def test_pairs_png_with_suffixed_pts_and_skips_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "a.png").write_bytes(b"x")
            (tmp / "a Panorama.pts").write_text("{}", encoding="utf-8")
            (tmp / "b.png").write_bytes(b"x")  # no .pts
            # prior outputs that must NOT be treated as source panos
            (tmp / "a.autolevel.pts").write_text("{}", encoding="utf-8")
            (tmp / "a.autolevel.overlay.png").write_bytes(b"x")

            pairs = pair_panos(tmp)
            names = [(img.name, pts.name if pts else None) for img, pts in pairs]
            self.assertEqual([("a.png", "a Panorama.pts"), ("b.png", None)], names)

    def test_pairs_plain_stem_pts_too(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "c.png").write_bytes(b"x")
            (tmp / "c.pts").write_text("{}", encoding="utf-8")
            pairs = pair_panos(tmp)
            self.assertEqual(1, len(pairs))
            self.assertEqual("c.pts", pairs[0][1].name)


class BatchTests(unittest.TestCase):
    def test_run_batch_robust_against_single_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            # pano with its own .pts
            write_roll_fixture(tmp / "with.png", width=2048, height=1024)
            (tmp / "with Panorama.pts").write_text(
                json.dumps(_sized_template(2048, 1024)), encoding="utf-8"
            )
            # pano without .pts -> template path
            write_roll_fixture(tmp / "without.png", width=2048, height=1024)
            # broken "pano" -> must not abort the run
            (tmp / "broken.png").write_bytes(b"not an image")

            result = run_batch(tmp, template=TEMPLATE_PTS, detect_kwargs={"reduce": 1})

            self.assertEqual(3, result["total"])
            by_name = {Path(r["image"]).name: r for r in result["results"]}
            self.assertEqual({"with.png", "without.png", "broken.png"}, set(by_name))
            self.assertEqual("fehler", by_name["broken.png"]["status"])
            self.assertNotEqual("fehler", by_name["with.png"]["status"])
            # successful panos produced outputs on disk
            self.assertTrue(Path(by_name["with.png"]["pts"]).exists())
            self.assertTrue(Path(by_name["without.png"]["pts"]).exists())
            self.assertEqual("pts", by_name["with.png"]["source"])
            self.assertEqual("template", by_name["without.png"]["source"])

    def test_pano_without_pts_and_no_template_is_skipped_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_roll_fixture(tmp / "lonely.png", width=2048, height=1024)
            result = run_batch(tmp, template=None, detect_kwargs={"reduce": 1})
            entry = result["results"][0]
            self.assertEqual("uebersprungen", entry["status"])

    def test_summary_separates_sicher_unsicher_fehler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_roll_fixture(tmp / "good.png", width=2048, height=1024)
            (tmp / "good Panorama.pts").write_text(
                json.dumps(_sized_template(2048, 1024)), encoding="utf-8"
            )
            (tmp / "broken.png").write_bytes(b"nope")
            result = run_batch(tmp, template=TEMPLATE_PTS, detect_kwargs={"reduce": 1})
            summary = result["summary"]
            self.assertEqual(1, summary["fehler"])
            self.assertEqual(1, summary["sicher"] + summary["unsicher"])
            self.assertEqual(result["total"], sum(summary.values()))


class BatchCliTests(unittest.TestCase):
    def test_batch_cli_runs_folder_and_reports_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_roll_fixture(tmp / "good.png", width=2048, height=1024)
            (tmp / "good Panorama.pts").write_text(
                json.dumps(_sized_template(2048, 1024)), encoding="utf-8"
            )
            (tmp / "broken.png").write_bytes(b"nope")
            out_dir = tmp / "out"

            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "panolevel",
                    "batch",
                    "--folder",
                    str(tmp),
                    "--template",
                    str(TEMPLATE_PTS),
                    "--out-dir",
                    str(out_dir),
                    "--reduce",
                    "1",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            rep = json.loads(proc.stdout)
            self.assertEqual(2, rep["total"])
            self.assertEqual(1, rep["summary"]["fehler"])
            self.assertEqual(sum(rep["summary"].values()), rep["total"])
            # outputs land in --out-dir
            self.assertTrue((out_dir / "good.autolevel.pts").exists())


TASKS = {
    "pair": PairTests,
    "batch": BatchTests,
    "cli": BatchCliTests,
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
