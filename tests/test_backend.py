"""Moteur d'Argos (sans GTK sauf GdkPixbuf) : lecture des dossiers, chargement, retouche par ImageMagick."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="argos-cfg-")
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp(prefix="argos-cache-")

from argos.backend import edit, folder, loader  # noqa: E402


def make(path, size="40x20", color="red", extra=()):
    subprocess.run(["convert", "-size", size, f"xc:{color}", *extra, path], check=True)


def pixel(path, x, y) -> str:
    out = subprocess.run(["convert", path + "[0]", "-format", f"%[pixel:p{{{x},{y}}}]", "info:"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def read(path, mode="rb"):
    with open(path, mode) as fh:
        return fh.read()


def dims(path) -> tuple[int, int]:
    out = subprocess.run(["identify", "-format", "%w %h", path + "[0]"], capture_output=True, text=True, check=True)
    w, h = out.stdout.split()
    return int(w), int(h)


class FolderTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_natural_order_and_filtering(self):
        for n in ("img10.jpg", "img2.jpg", "img1.PNG", "notes.txt", ".cache.jpg"):
            open(os.path.join(self.d, n), "wb").close()
        os.mkdir(os.path.join(self.d, "Vacances"))
        dirs, images = folder.scan(self.d)
        self.assertEqual([e.name for e in dirs], ["Vacances"])
        self.assertEqual([e.name for e in images], ["img1.PNG", "img2.jpg", "img10.jpg"])
        _d, hidden = folder.scan(self.d, show_hidden=True)
        self.assertIn(".cache.jpg", [e.name for e in hidden])

    def test_sort_by_date_and_reverse(self):
        for i, n in enumerate(("b.jpg", "a.jpg", "c.jpg")):
            p = os.path.join(self.d, n)
            open(p, "wb").close()
            os.utime(p, (1000 + i, 1000 + i))
        _d, images = folder.scan(self.d, sort_key="date")
        self.assertEqual([e.name for e in images], ["b.jpg", "a.jpg", "c.jpg"])
        _d, images = folder.scan(self.d, sort_key="name", reverse=True)
        self.assertEqual([e.name for e in images], ["c.jpg", "b.jpg", "a.jpg"])

    def test_non_utf8_name(self):
        raw = os.path.join(os.fsencode(self.d), b"caf\xe9.jpg")
        open(raw, "wb").close()
        _d, images = folder.scan(self.d)
        self.assertEqual(len(images), 1)


class LoaderTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_exif_orientation_applied(self):
        p = os.path.join(self.d, "tel.jpg")
        make(p, "40x20")
        import gi
        gi.require_version("GExiv2", "0.10")
        from gi.repository import GExiv2
        md = GExiv2.Metadata()
        md.open_path(p)
        md.try_set_tag_string("Exif.Image.Orientation", "6")     # 6 = à tourner d'un quart à droite
        md.save_file(p)
        pic = loader.load(p)
        self.assertEqual((pic.width, pic.height), (20, 40))
        self.assertEqual(pic.format, "JPEG")

    def test_animation_kept(self):
        p = os.path.join(self.d, "a.gif")
        subprocess.run(["convert", "-size", "8x8", "xc:red", "xc:blue", "-set", "delay", "20", "-loop", "0", p],
                       check=True)
        pic = loader.load(p)
        self.assertIsNotNone(pic.animation)

    def test_unreadable_gives_error(self):
        p = os.path.join(self.d, "faux.jpg")
        with open(p, "w") as fh:
            fh.write("pas une image")
        pic = loader.load(p)
        self.assertTrue(pic.error)
        self.assertIsNone(pic.texture)


class EditTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.src = os.path.join(self.d, "photo.png")
        # 40 × 20 : moitié gauche rouge, moitié droite bleue
        subprocess.run(["convert", "-size", "20x20", "xc:red", "-size", "20x20", "xc:blue", "+append", self.src],
                       check=True)

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_rotate_replace(self):
        os.chmod(self.src, 0o640)
        out = edit.apply(self.src, self.src, rotation=1, replace=True)
        self.assertEqual(out, self.src)
        self.assertEqual(dims(self.src), (20, 40))
        self.assertTrue(pixel(self.src, 10, 5).startswith("srgb(255,0,0)") or "red" in pixel(self.src, 10, 5))
        self.assertEqual(os.stat(self.src).st_mode & 0o777, 0o640)
        self.assertEqual([n for n in os.listdir(self.d) if n.startswith(".argos-")], [])

    def test_crop_after_rotation(self):
        # tournée d'un quart : rouge en haut (0..20), bleu en bas ; on garde le bas
        out = edit.apply(self.src, os.path.join(self.d, "bas.png"), rotation=1, crop=(0, 20, 20, 20))
        self.assertEqual(dims(out), (20, 20))
        self.assertIn("0,0,255", pixel(out, 10, 10).replace("blue", "0,0,255"))

    def test_new_name_never_overwrites(self):
        target = os.path.join(self.d, "copie.jpg")
        open(target, "wb").close()
        out = edit.apply(self.src, target)
        self.assertEqual(os.path.basename(out), "copie (2).jpg")
        self.assertEqual(os.path.getsize(target), 0)
        self.assertEqual(subprocess.run(["identify", "-format", "%m", out], capture_output=True, text=True).stdout,
                         "JPEG")

    def test_backup_restore(self):
        before = read(self.src)
        copy = edit.backup(self.src)
        edit.apply(self.src, self.src, rotation=2, replace=True)
        self.assertNotEqual(read(self.src), before)
        edit.restore(copy, self.src)
        self.assertEqual(read(self.src), before)
        self.assertFalse(os.path.exists(copy))

    def test_jpeg_keeps_quality(self):
        jpg = os.path.join(self.d, "q.jpg")
        subprocess.run(["convert", "-size", "64x64", "plasma:", "-quality", "75", jpg], check=True)
        edit.apply(jpg, jpg, rotation=1, replace=True)
        q = subprocess.run(["identify", "-format", "%Q", jpg], capture_output=True, text=True).stdout
        self.assertEqual(q, "75")

    def test_failure_leaves_original(self):
        bad = os.path.join(self.d, "faux.png")
        with open(bad, "w") as fh:
            fh.write("rien")
        with self.assertRaises(edit.EditError):
            edit.apply(bad, bad, rotation=1, replace=True)
        self.assertEqual(read(bad, "r"), "rien")
        self.assertEqual([n for n in os.listdir(self.d) if n.startswith(".argos-")], [])

    def test_sweep(self):
        copy = edit.backup(self.src)
        os.utime(copy, (time.time() - 3 * 86400,) * 2)
        edit.sweep_backups()
        self.assertFalse(os.path.exists(copy))


if __name__ == "__main__":
    unittest.main()
