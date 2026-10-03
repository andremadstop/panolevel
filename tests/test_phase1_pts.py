from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from panolevel.ptsio import (
    image_size,
    insert_vertical_lines,
    list_vertical_lines,
    load_pts,
    position_params,
    save_pts_copy,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures"
# Only used to (re)create missing fixtures from the original PTGui projects.
SOURCE_DIR = Path(os.environ.get("PANOLEVEL_SOURCE_DIR", ""))

FIXTURES = {
    "2026-06-12_10.43.40 Panorama.pts": {
        "vertical_lines": 4,
        "size": [16384, 8192],
        "source_sha256": "9c5d43139b941c0a56757533c83d81a6bdc2647f8cdcfa98af4d2872ee792131",
    },
    "2026-06-12_13.44.31 Panorama.pts": {
        "vertical_lines": 3,
        "size": [16384, 8192],
        "source_sha256": "b950bc81f5c3d14a6faf43e19c94d35264007ba732a8e1853c1857bed00ee290",
    },
}

NEW_LINES = [((111, 222), (111, 555)), ((4444, 3333), (4446, 3777))]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_fixture_copies() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name in FIXTURES:
        source = SOURCE_DIR / name
        target = FIXTURE_DIR / name
        if not target.exists():
            shutil.copy2(source, target)


class FixtureHashMixin(unittest.TestCase):
    def setUp(self) -> None:
        ensure_fixture_copies()
        self.source_hashes_before = self._source_hashes()
        self.fixture_hashes_before = self._fixture_hashes()

    def tearDown(self) -> None:
        self.assertEqual(self.source_hashes_before, self._source_hashes())
        self.assertEqual(self.fixture_hashes_before, self._fixture_hashes())

    def _source_hashes(self) -> dict[str, str]:
        # Integrity watch over the COMMITTED repo fixtures, not the external
        # originals in SOURCE_DIR: André edits those panos in PTGui legitimately
        # (they are not a test invariant). The committed fixtures carry the known
        # original content; the setUp/tearDown before==after check still proves a
        # test run never mutates them.
        hashes = {}
        for name, expected in FIXTURES.items():
            fixture = FIXTURE_DIR / name
            self.assertTrue(fixture.exists(), f"Missing repo fixture: {fixture}")
            actual = sha256(fixture)
            self.assertEqual(expected["source_sha256"], actual)
            hashes[name] = actual
        return hashes

    def _fixture_hashes(self) -> dict[str, str]:
        hashes = {}
        for name in FIXTURES:
            fixture = FIXTURE_DIR / name
            self.assertTrue(fixture.exists(), f"Missing repo fixture: {fixture}")
            hashes[name] = sha256(fixture)
        return hashes


class LoadTests(FixtureHashMixin):
    def test_load_real_pts_and_vertical_lines(self) -> None:
        for name, expected in FIXTURES.items():
            with self.subTest(name=name):
                doc = load_pts(FIXTURE_DIR / name)
                self.assertEqual(expected["size"], image_size(doc))
                params = position_params(doc)
                for key in ("yaw", "pitch", "roll"):
                    self.assertIn(key, params)
                    self.assertIsInstance(params[key], (int, float))
                self.assertEqual(expected["vertical_lines"], len(list_vertical_lines(doc)))


class WriteTests(FixtureHashMixin):
    def test_insert_and_save_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "sample.pts"
            shutil.copy2(FIXTURE_DIR / "2026-06-12_13.44.31 Panorama.pts", src)
            before = sha256(src)

            doc = load_pts(src)
            original_count = len(list_vertical_lines(doc))
            inserted = insert_vertical_lines(doc, NEW_LINES)
            out = save_pts_copy(doc, src)

            self.assertEqual(len(NEW_LINES), inserted)
            self.assertEqual(before, sha256(src))
            self.assertTrue(out.exists())
            written = load_pts(out)
            self.assertEqual(original_count + len(NEW_LINES), len(list_vertical_lines(written)))
            self.assertEqual(image_size(doc), image_size(written))


class IdempotentTests(FixtureHashMixin):
    def test_repeated_insert_does_not_duplicate_existing_lines(self) -> None:
        doc = load_pts(FIXTURE_DIR / "2026-06-12_10.43.40 Panorama.pts")
        manual_count = len(list_vertical_lines(doc))
        inserted_first = insert_vertical_lines(doc, NEW_LINES)
        inserted_second = insert_vertical_lines(doc, NEW_LINES)

        self.assertEqual(len(NEW_LINES), inserted_first)
        self.assertEqual(0, inserted_second)
        self.assertEqual(manual_count + len(NEW_LINES), len(list_vertical_lines(doc)))

    def test_manual_controlpoints_are_preserved(self) -> None:
        doc = load_pts(FIXTURE_DIR / "2026-06-12_13.44.31 Panorama.pts")
        before = copy.deepcopy(doc["project"]["controlpoints"])
        insert_vertical_lines(doc, NEW_LINES)
        self.assertEqual(before, doc["project"]["controlpoints"][: len(before)])


class CliTests(FixtureHashMixin):
    def test_inject_and_inspect_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "sample.pts"
            out = Path(tmp) / "sample.injected.pts"
            shutil.copy2(FIXTURE_DIR / "2026-06-12_13.44.31 Panorama.pts", src)
            before = sha256(src)

            inject = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "panolevel",
                    "inject",
                    "--pts",
                    str(src),
                    "--lines",
                    "100,200,100,500;2000,3000,2001,3500",
                    "--out",
                    str(out),
                ],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(0, inject.returncode, inject.stderr)
            payload = json.loads(inject.stdout)
            self.assertEqual(str(out), payload["output"])
            self.assertEqual(2, payload["inserted_lines"])
            self.assertEqual(before, sha256(src))

            inspect = subprocess.run(
                [sys.executable, "-m", "panolevel", "inspect", "--pts", str(out)],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(0, inspect.returncode, inspect.stderr)
            inspected = json.loads(inspect.stdout)
            self.assertEqual([16384, 8192], inspected["image_size"])
            self.assertEqual(5, len(inspected["vertical_lines"]))


TASKS = {
    "load": LoadTests,
    "write": WriteTests,
    "idempotent": IdempotentTests,
    "cli": CliTests,
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
