"""Tests fuer den Ordner-Modus (unittest-Stil, laeuft auch unter pytest)."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
from fastapi.testclient import TestClient

from panolevel import folder as F
from panolevel import quality
from panolevel.server import create_folder_app
from panolevel.synthetic import write_roll_fixture

LINES = [[100, 100, 100, 300], [400, 100, 402, 300], [700, 100, 698, 300]]
HAVE_EXIFTOOL = shutil.which("exiftool") is not None


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.folder = self.root / "stitched"
        self.folder.mkdir()
        self.out = self.root / "gerade"
        for name, roll in (("pano_10.png", 6.0), ("pano_2.png", 4.0), ("pano_1.png", 8.0)):
            write_roll_fixture(self.folder / name, width=2048, height=1024, roll_deg=roll)
        (self.folder / "notiz.txt").write_text("x")
        (self.folder / ".versteckt.png").write_bytes(b"x")

    def tearDown(self):
        self._tmp.cleanup()

    def client(self, **kw):
        kw.setdefault("preview_reduce", 2)
        kw.setdefault("detect_kwargs", {"reduce": 1})
        app = create_folder_app(self.folder, self.out, **kw)
        return TestClient(app), app.state.session

    def idx(self, sess, name):
        return [p.name for p in sess.images].index(name)


class ListTests(Base):
    def test_natural_sort_and_filter(self):
        names = [p.name for p in F.list_images(self.folder)]
        self.assertEqual(["pano_1.png", "pano_2.png", "pano_10.png"], names)

    def test_extensions_case_insensitive(self):
        (self.folder / "A.JPG").write_bytes(b"x")
        (self.folder / "b.TIF").write_bytes(b"x")
        (self.folder / "c.tiff").write_bytes(b"x")
        (self.folder / "d.gif").write_bytes(b"x")
        self.assertEqual(
            ["A.JPG", "b.TIF", "c.tiff"], [p.name for p in F.list_images(self.folder)][:3]
        )
        self.assertNotIn("d.gif", [p.name for p in F.list_images(self.folder)])

    def test_default_out_dir_is_sibling(self):
        self.assertEqual(self.root / "panolevel_gerade", F.default_out_dir(self.folder))

    def test_out_dir_equal_input_refused(self):
        with self.assertRaises(ValueError):
            F.resolve_out_dir(self.folder, self.folder)
        with self.assertRaises(ValueError):
            create_folder_app(self.folder, self.folder)

    def test_empty_folder(self):
        e = self.root / "leer"
        e.mkdir()
        with self.assertRaises(FileNotFoundError):
            create_folder_app(e, self.out)


class StatusTests(Base):
    def test_roundtrip_and_resume(self):
        c, s = self.client()
        c.post("/api/folder/0/skip")
        data = json.loads((self.out / "panolevel_status.json").read_text())
        self.assertEqual("skipped", data["images"]["pano_1.png"]["status"])
        self.assertIn("settings", data)
        c2, s2 = self.client()
        lst = c2.get("/api/folder").json()
        self.assertEqual("skipped", lst["images"][0]["status"])
        self.assertEqual(1, lst["first_open"])

    def test_corrupt_json_is_set_aside(self):
        self.out.mkdir()
        (self.out / "panolevel_status.json").write_text("{kaputt")
        c, s = self.client()
        self.assertEqual(0, c.get("/api/folder").json()["done"])
        self.assertTrue(list(self.out.glob("panolevel_status.json.defekt-*")))

    def test_done_requires_output_file(self):
        c, s = self.client()
        self.assertTrue(c.post("/api/folder/0/export", json={"lines": LINES}).json()["ok"])
        self.assertEqual(1, c.get("/api/folder").json()["done"])
        (self.out / "pano_1.png").unlink()
        self.assertEqual(0, c.get("/api/folder").json()["done"])

    def test_settings_validation_and_persistence(self):
        c, s = self.client()
        self.assertFalse(c.post("/api/settings", json={"format": "bmp"}).json()["ok"])
        self.assertFalse(c.post("/api/settings", json={"jpg_quality": 50}).json()["ok"])
        self.assertFalse(c.post("/api/settings", json={"pts": True}).json()["ok"])
        r = c.post(
            "/api/settings", json={"format": "jpg", "jpg_quality": 90, "suffix": True}
        ).json()
        self.assertTrue(r["ok"])
        c2, _ = self.client()
        st = c2.get("/api/folder").json()["settings"]
        self.assertEqual(
            ("jpg", 90, True, True),
            (st["format"], st["jpg_quality"], st["suffix"], st["keep_metadata"]),
        )


class ApiSafetyTests(Base):
    def test_index_bounds_404(self):
        c, s = self.client()
        for i in (-1, 3, 999):
            self.assertEqual(404, c.post(f"/api/folder/{i}/load").status_code)
            self.assertEqual(404, c.post(f"/api/folder/{i}/skip").status_code)
            self.assertEqual(
                404, c.post(f"/api/folder/{i}/estimate", json={"lines": LINES}).status_code
            )
            self.assertEqual(
                404, c.post(f"/api/folder/{i}/export", json={"lines": LINES}).status_code
            )
            self.assertEqual(404, c.get(f"/api/folder/{i}/crop?x0=0&y0=0&x1=10&y1=10").status_code)

    def test_no_free_path_from_client(self):
        c, s = self.client()
        r = c.post(
            "/api/folder/0/export",
            json={"lines": LINES, "out": "/tmp/boese.png", "path": "/etc/passwd"},
        )
        self.assertTrue(r.json()["ok"])
        self.assertFalse(Path("/tmp/boese.png").exists())
        self.assertEqual([self.out / "pano_1.png"], [p for p in self.out.glob("*.png")])

    def test_out_path_stays_in_out(self):
        c, s = self.client()
        for i in range(3):
            self.assertEqual(self.out.resolve(), s.out_path(i).parent)
        s.images[0] = self.folder / ".." / "x.png"  # Pfad-Trick
        with mock.patch.object(F, "output_name", return_value="../../ausbruch.png"):
            with self.assertRaises(ValueError):
                s.out_path(0)

    def test_host_header_check(self):
        app = create_folder_app(
            self.folder, self.out, preview_reduce=2, allowed_hosts=["127.0.0.1", "localhost"]
        )
        c = TestClient(app)
        self.assertEqual(200, c.get("/api/folder", headers={"host": "127.0.0.1:8765"}).status_code)
        self.assertEqual(400, c.get("/api/folder", headers={"host": "boese.example"}).status_code)

    def test_load_returns_preview_and_lines(self):
        c, s = self.client()
        d = c.post("/api/folder/0/load").json()
        self.assertTrue(d["ok"])
        self.assertEqual((1024, 512), (d["width"], d["height"]))
        self.assertGreaterEqual(len(d["lines"]), 3)
        self.assertEqual((2048, 1024), (d["full_width"], d["full_height"]))

    def test_estimate_has_quality(self):
        c, s = self.client()
        lines = c.post("/api/folder/0/load").json()["lines"]
        d = c.post("/api/folder/0/estimate", json={"lines": lines}).json()
        self.assertTrue(d["ok"])
        self.assertIn(d["quality"], ("gut", "pruefen"))
        self.assertLess(abs(d["roll"] - 8.0), 1.5)


class CropTests(Base):
    def test_crop_returns_jpeg_and_rect(self):
        c, s = self.client()
        r = c.get("/api/folder/0/crop?x0=100&y0=50&x1=300&y1=150&w=400")
        self.assertEqual(200, r.status_code)
        img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(400, img.shape[1])
        self.assertEqual(
            [100, 50, 300, 150], [round(float(v)) for v in r.headers["x-rect"].split(",")]
        )

    def test_crop_wraps_over_seam(self):
        c, s = self.client()
        full = cv2.imread(str(self.folder / "pano_1.png"))
        r = c.get("/api/folder/0/crop?x0=1000&y0=0&x1=1048&y1=100&w=4000")
        img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(96, img.shape[1])  # 48 Vorschau-px * 2
        # linke Haelfte aus Spalten 2000..2047, rechte aus 0..47 (JPEG-Toleranz)
        left = full[:200, 2000:2048].astype(int)
        right = full[:200, 0:48].astype(int)
        want = np.concatenate([left, right], axis=1)
        self.assertLess(np.abs(img.astype(int) - want).mean(), 12)

    def test_crop_clamps_vertical_and_reports_rect(self):
        c, s = self.client()
        r = c.get("/api/folder/0/crop?x0=0&y0=-50&x1=100&y1=1000")
        rect = [float(v) for v in r.headers["x-rect"].split(",")]
        self.assertEqual((0.0, 512.0), (rect[1], rect[3]))

    def test_only_one_full_image_cached(self):
        c, s = self.client()
        c.get("/api/folder/0/crop?x0=0&y0=0&x1=10&y1=10")
        self.assertEqual(0, s._full[0])
        c.get("/api/folder/1/crop?x0=0&y0=0&x1=10&y1=10")
        self.assertEqual(1, s._full[0])
        c.post("/api/folder/2/load")
        self.assertIsNone(s._full)


class ExportTests(Base):
    def test_export_creates_file_original_untouched(self):
        before = {p.name: sha(p) for p in self.folder.iterdir()}
        c, s = self.client()
        r = c.post("/api/folder/0/export", json={"lines": LINES, "yaw": 10}).json()
        self.assertTrue(r["ok"], r)
        self.assertTrue((self.out / "pano_1.png").is_file())
        self.assertEqual(before, {p.name: sha(p) for p in self.folder.iterdir()})
        rec = json.loads((self.out / "panolevel_status.json").read_text())["images"]["pano_1.png"]
        self.assertEqual(("done", 3, 10), (rec["status"], rec["lines"], rec["yaw"]))
        self.assertIn(rec["rating"], ("gut", "pruefen"))

    def test_needs_three_lines(self):
        c, s = self.client()
        r = c.post("/api/folder/0/export", json={"lines": LINES[:2]}).json()
        self.assertFalse(r["ok"])
        self.assertFalse(self.out.exists() and any(self.out.glob("*.png")))

    def test_no_overwrite_without_confirmation(self):
        c, s = self.client()
        c.post("/api/folder/0/export", json={"lines": LINES})
        mt = (self.out / "pano_1.png").stat().st_mtime_ns
        r = c.post("/api/folder/0/export", json={"lines": LINES}).json()
        self.assertFalse(r["ok"])
        self.assertTrue(r["exists"])
        self.assertEqual(mt, (self.out / "pano_1.png").stat().st_mtime_ns)
        r = c.post("/api/folder/0/export", json={"lines": LINES, "overwrite": True}).json()
        self.assertTrue(r["ok"])

    def test_format_jpg_quality_and_suffix(self):
        c, s = self.client()
        c.post(
            "/api/settings",
            json={"format": "jpg", "jpg_quality": 100, "suffix": True, "keep_metadata": False},
        )
        r = c.post("/api/folder/0/export", json={"lines": LINES}).json()
        self.assertTrue(r["ok"], r)
        p = self.out / "pano_1_gerade.jpg"
        self.assertTrue(p.is_file())
        c.post("/api/settings", json={"jpg_quality": 80})
        c.post("/api/folder/1/export", json={"lines": LINES})
        self.assertGreater(p.stat().st_size, (self.out / "pano_2_gerade.jpg").stat().st_size)

    def test_format_tiff16_and_png(self):
        c, s = self.client()
        c.post("/api/settings", json={"format": "tiff16", "keep_metadata": False})
        self.assertTrue(c.post("/api/folder/0/export", json={"lines": LINES}).json()["ok"])
        t = cv2.imread(str(self.out / "pano_1.tif"), cv2.IMREAD_UNCHANGED)
        self.assertEqual(np.uint16, t.dtype)
        c.post("/api/settings", json={"format": "png"})
        self.assertTrue(c.post("/api/folder/1/export", json={"lines": LINES}).json()["ok"])
        p = cv2.imread(str(self.out / "pano_2.png"), cv2.IMREAD_UNCHANGED)
        self.assertEqual(np.uint8, p.dtype)

    def test_original_format_keeps_extension(self):
        img = cv2.imread(str(self.folder / "pano_1.png"))
        cv2.imwrite(str(self.folder / "pano_3.jpg"), img)
        c, s = self.client()
        i = self.idx(s, "pano_3.jpg")
        self.assertTrue(c.post(f"/api/folder/{i}/export", json={"lines": LINES}).json()["ok"])
        self.assertTrue((self.out / "pano_3.jpg").is_file())

    def test_name_collision_refused(self):
        img = cv2.imread(str(self.folder / "pano_1.png"))
        cv2.imwrite(str(self.folder / "pano_1.jpg"), img)
        c, s = self.client()
        c.post("/api/settings", json={"format": "tiff16", "keep_metadata": False})
        r = c.post("/api/folder/0/export", json={"lines": LINES}).json()
        self.assertFalse(r["ok"])
        self.assertFalse(list(self.out.glob("*.tif")))

    def test_pts_export_with_template(self):
        tpl = Path(__file__).parent / "fixtures" / "2026-06-12_10.43.40 Panorama.pts"
        c, s = self.client(template=tpl)
        self.assertTrue(c.post("/api/settings", json={"pts": True}).json()["ok"])
        r = c.post("/api/folder/0/export", json={"lines": LINES}).json()
        self.assertTrue(r["ok"], r)
        self.assertTrue((self.out / "pano_1.pts").is_file())

    def test_report_csv(self):
        c, s = self.client()
        c.post("/api/folder/0/export", json={"lines": LINES})
        c.post("/api/folder/1/skip")
        p = Path(c.post("/api/report").json()["path"])
        self.assertEqual(self.out / "panolevel_bericht.csv", p)
        rows = p.read_text(encoding="utf-8-sig").splitlines()
        self.assertTrue(rows[0].startswith("Datei;Status;Ausgabedatei;Linien"))
        self.assertEqual(4, len(rows))
        self.assertIn("pano_1.png;erledigt;pano_1.png;3;", rows[1])
        self.assertIn("pano_2.png;uebersprungen", rows[2])
        self.assertIn("pano_10.png;offen", rows[3])

    def test_open_out_only_out(self):
        c, s = self.client()
        with mock.patch.object(F, "_open_dir") as m:
            c.post("/api/open-out")
        m.assert_called_once_with(self.out.resolve())


@unittest.skipUnless(HAVE_EXIFTOOL, "exiftool fehlt")
class MetadataTests(Base):
    def setUp(self):
        super().setUp()
        img = cv2.imread(str(self.folder / "pano_1.png"))
        self.src = self.folder / "geo.jpg"
        cv2.imwrite(str(self.src), img)
        subprocess.run(
            [
                "exiftool",
                "-overwrite_original",
                "-GPSLatitude=52.4",
                "-GPSLatitudeRef=N",
                "-GPSLongitude=13.1",
                "-GPSLongitudeRef=E",
                "-Make=Xphase",
                "-DateTimeOriginal=2026:10:03 07:18:16",
                "-XMP-GPano:ProjectionType=equirectangular",
                "-XMP-GPano:FullPanoWidthPixels=2048",
                "-XMP-GPano:FullPanoHeightPixels=1024",
                str(self.src),
            ],
            check=True,
            capture_output=True,
        )
        self.before = sha(self.src)

    def tags(self, p):
        r = subprocess.run(
            ["exiftool", "-j", "-G1", "-n", str(p)], capture_output=True, text=True, check=True
        )
        return json.loads(r.stdout)[0]

    def test_metadata_kept_and_gpano_present(self):
        c, s = self.client()
        i = self.idx(s, "geo.jpg")
        r = c.post(f"/api/folder/{i}/export", json={"lines": LINES}).json()
        self.assertTrue(r["ok"], r)
        self.assertIsNone(r["warning"])
        t = self.tags(self.out / "geo.jpg")
        self.assertAlmostEqual(52.4, t["GPS:GPSLatitude"], places=3)
        self.assertEqual("Xphase", t["IFD0:Make"])
        self.assertEqual("2026:10:03 07:18:16", t["ExifIFD:DateTimeOriginal"])
        self.assertEqual("equirectangular", t["XMP-GPano:ProjectionType"])
        self.assertEqual(2048, t["XMP-GPano:FullPanoWidthPixels"])
        self.assertIn("PanoLevel", t["XMP-photoshop:Instructions"])
        self.assertIn("Linien=3", t["XMP-photoshop:Instructions"])
        self.assertEqual(self.before, sha(self.src))

    def test_gpano_added_when_missing_and_for_png(self):
        c, s = self.client()
        c.post("/api/settings", json={"format": "png"})
        i = self.idx(s, "pano_1.png")
        r = c.post(f"/api/folder/{i}/export", json={"lines": LINES}).json()
        self.assertTrue(r["ok"], r)
        t = self.tags(self.out / "pano_1.png")
        self.assertEqual("equirectangular", t["XMP-GPano:ProjectionType"])
        self.assertEqual(2048, t["XMP-GPano:FullPanoWidthPixels"])

    def test_metadata_off(self):
        c, s = self.client()
        c.post("/api/settings", json={"keep_metadata": False})
        i = self.idx(s, "geo.jpg")
        c.post(f"/api/folder/{i}/export", json={"lines": LINES})
        t = self.tags(self.out / "geo.jpg")
        self.assertNotIn("GPS:GPSLatitude", t)

    def test_no_temp_files_left(self):
        c, s = self.client()
        c.post("/api/folder/0/export", json={"lines": LINES})
        self.assertFalse(
            [p for p in self.out.iterdir() if ".tmp" in p.name or p.name.endswith("_original")]
        )


class QualityTests(unittest.TestCase):
    def test_residuals_zero_for_perfect_lines(self):
        z = np.array([0, 1.0, 0])
        n = np.array([[1, 0, 0], [0, 0, 1.0]])
        np.testing.assert_allclose(quality.line_residuals_deg(n, z), [0, 0], atol=1e-9)

    def test_residual_angle(self):
        z = np.array([0, 1.0, 0])
        n = np.array([[np.cos(np.radians(89)), np.sin(np.radians(1)), 0]])
        self.assertAlmostEqual(1.0, float(quality.line_residuals_deg(n, z)[0]), places=6)

    def test_rating(self):
        self.assertEqual("gut", quality.rate([0.1, 0.2, 0.3])[0])
        self.assertEqual("pruefen", quality.rate([0.1, 0.2])[0])  # zu wenige Linien
        self.assertEqual("pruefen", quality.rate([0.1, 0.1, 3.0])[0])  # Ausreisser
        self.assertEqual("pruefen", quality.rate([1.5, 1.5, 1.5])[0])  # Rest zu gross
        self.assertEqual("pruefen", quality.rate([])[0])


class CliTests(Base):
    def run_cli(self, *argv):
        from panolevel import cli

        with mock.patch("uvicorn.run") as run, mock.patch("webbrowser.open"):
            rc = cli.main(["serve", *map(str, argv), "--no-open"])
        return rc, run

    def test_positional_folder(self):
        rc, run = self.run_cli(self.folder, "--out-dir", self.out, "--port", "9999")
        self.assertEqual(0, rc)
        app = run.call_args.args[0]
        self.assertEqual(self.out.resolve(), app.state.session.out_dir)
        self.assertEqual("127.0.0.1", run.call_args.kwargs["host"])
        self.assertEqual(9999, run.call_args.kwargs["port"])

    def test_folder_option_and_default_out(self):
        rc, run = self.run_cli("--folder", self.folder)
        self.assertEqual(
            self.root / "panolevel_gerade", run.call_args.args[0].state.session.out_dir
        )

    def test_image_still_works(self):
        rc, run = self.run_cli("--image", self.folder / "pano_1.png", "--reduce", "1")
        self.assertEqual(0, rc)
        self.assertFalse(hasattr(run.call_args.args[0].state, "session"))

    def test_conflicts_and_missing(self):
        with self.assertRaises(SystemExit):
            self.run_cli(self.folder, "--image", self.folder / "pano_1.png")
        with self.assertRaises(SystemExit):
            self.run_cli()
        with self.assertRaises(SystemExit):
            self.run_cli(self.folder, "--out-dir", self.folder)


if __name__ == "__main__":
    unittest.main()
