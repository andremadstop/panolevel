"""Startet die Zoom-Mathematik-Tests (node --test); ohne node uebersprungen."""

import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "node fehlt")
class ViewMathNodeTests(unittest.TestCase):
    def test_node_suite(self):
        r = subprocess.run(
            ["node", "--test", str(Path(__file__).parent / "viewmath.test.mjs")],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(0, r.returncode, r.stdout[-2000:] + r.stderr[-1000:])
