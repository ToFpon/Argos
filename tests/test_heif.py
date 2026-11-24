"""AVIF / HEIC : taille lue dans l'EN-TÊTE, sans décoder (le loader heif de GdkPixbuf décode l'image entière pour
get_file_info : grands dossiers d'AVIF très lents, vu chez Tof)."""

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from argos.backend import loader  # noqa: E402


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def ispe(w: int, h: int) -> bytes:
    return box(b"ispe", b"\0\0\0\0" + struct.pack(">II", w, h))


class HeifHeaderTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def write(self, name, data):
        p = os.path.join(self.d, name)
        with open(p, "wb") as fh:
            fh.write(box(b"ftyp", b"avif\0\0\0\0mif1") + box(b"meta", data) + box(b"mdat", b"\0" * 64))
        return p

    def test_grid_tiles_and_thumbnail(self):
        """HEIC de téléphone : tuiles 512 × 512, vignette 320 × 240, image entière (grille) 4032 × 3024."""
        p = self.write("tel.heic", ispe(512, 512) * 3 + ispe(320, 240) + ispe(4032, 3024))
        self.assertEqual(loader.image_size(p), (4032, 3024))
        self.assertEqual(loader.format_name(p), "HEIC")

    def test_quarter_turn(self):
        p = self.write("portrait.avif", ispe(4000, 3000) + box(b"irot", b"\x01"))
        self.assertEqual(loader.image_size(p), (3000, 4000))
        p = self.write("demi.avif", ispe(4000, 3000) + box(b"irot", b"\x02"))
        self.assertEqual(loader.image_size(p), (4000, 3000))

    def test_no_header_falls_back(self):
        p = self.write("vide.avif", b"")
        self.assertIsNone(loader.image_size(p))

    @unittest.skipUnless(shutil.which("heif-enc") and shutil.which("convert"), "heif-enc (libheif-examples)")
    def test_real_avif_is_not_decoded(self):
        src = os.path.join(self.d, "src.png")
        subprocess.run(["convert", "-size", "1200x800", "plasma:", src], check=True, capture_output=True)
        avif = os.path.join(self.d, "p.avif")
        subprocess.run(["heif-enc", "-A", "-q", "50", src, "-o", avif], check=True, capture_output=True)
        with mock.patch.object(loader.GdkPixbuf.Pixbuf, "get_file_info",
                               side_effect=AssertionError("get_file_info décode l'AVIF entier")):
            t = time.monotonic()
            self.assertEqual(loader.image_size(avif), (1200, 800))
            self.assertEqual(loader.format_name(avif), "AVIF")
            self.assertLess(time.monotonic() - t, 0.05)
        if any("avif" in f.get_mime_types()[0] or "heif" in f.get_name() for f in loader.GdkPixbuf.Pixbuf.get_formats()):
            pic = loader.load(avif)
            self.assertEqual((pic.width, pic.height, pic.error), (1200, 800, ""))


class CacheWeightTest(unittest.TestCase):
    def test_big_pictures_evicted_by_weight(self):
        """Visionneuse : 7 photos décodées au plus, et 640 Mo de pixels au plus (48 Mpx = 192 Mo chacune)."""
        lo = loader.Loader()
        big = [loader.Picture(f"/p{i}", object(), 8000, 6000, 0, "AVIF") for i in range(6)]
        with mock.patch.object(loader, "load", side_effect=lambda p: next(b for b in big if b.path == p)):
            for b in big:
                done = []
                lo.request(b.path, done.append)
                for _ in range(200):
                    if lo.cached(b.path) is not None:
                        break
                    time.sleep(0.01)
        self.assertLessEqual(loader._weight(lo._cache), loader.CACHE_BYTES)
        self.assertIsNotNone(lo.cached("/p5"), "la dernière lue reste")


if __name__ == "__main__":
    unittest.main()
