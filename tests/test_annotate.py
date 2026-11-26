"""Annoter (repris d'Odysseus) : enregistrement sûr de l'image dessinée (`edit.write_surface`)."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import cairo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gi  # noqa: E402

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

from argos.backend import edit  # noqa: E402

try:
    gi.require_version("GExiv2", "0.10")
    from gi.repository import GExiv2  # noqa: E402
except (ImportError, ValueError):
    GExiv2 = None


def surface(w=60, h=40, rgb=(0, 0, 1)):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    c = cairo.Context(s)
    c.set_source_rgb(*rgb)
    c.paint()
    s.flush()
    return s


def pixel(path, x, y):
    pb = GdkPixbuf.Pixbuf.new_from_file(path)
    data = pb.get_pixels()
    i = y * pb.get_rowstride() + x * pb.get_n_channels()
    return tuple(data[i:i + 3]), (pb.get_width(), pb.get_height()), pb.get_has_alpha()


class WriteSurfaceTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_png_new_name_never_overwrites(self):
        dest = os.path.join(self.d, "photo (annotée).png")
        with open(dest, "wb") as fh:
            fh.write(b"autre")
        written = edit.write_surface(surface(), dest)
        self.assertEqual(os.path.basename(written), "photo (annotée) (2).png")
        with open(dest, "rb") as fh:
            self.assertEqual(fh.read(), b"autre")
        self.assertEqual(pixel(written, 5, 5)[0], (0, 0, 255))
        self.assertEqual([f for f in os.listdir(self.d) if f.startswith(".argos-")], [])

    def test_jpeg_replace_rgb_colours_and_size(self):
        dest = os.path.join(self.d, "photo.jpg")
        subprocess.run(["convert", "-size", "60x40", "xc:green", dest], check=True)
        os.chmod(dest, 0o640)
        written = edit.write_surface(surface(rgb=(1, 0, 0)), dest, replace=True)
        self.assertEqual(written, dest)
        (r, g, b), size, alpha = pixel(dest, 30, 20)
        self.assertEqual(size, (60, 40))
        self.assertFalse(alpha)
        self.assertTrue(r > 240 and g < 20 and b < 20, (r, g, b))      # rouge, pas bleu (ordre BGRA de cairo)
        self.assertEqual(os.stat(dest).st_mode & 0o777, 0o640)

    def test_symlink_keeps_the_link(self):
        real = os.path.join(self.d, "vraie.png")
        edit.write_surface(surface(rgb=(0, 1, 0)), real)
        link = os.path.join(self.d, "lien.png")
        os.symlink("vraie.png", link)
        edit.write_surface(surface(), link, replace=True)
        self.assertTrue(os.path.islink(link))
        self.assertEqual(pixel(real, 1, 1)[0], (0, 0, 255))

    def test_unwritable_format_refused(self):
        with self.assertRaises(edit.EditError):
            edit.write_surface(surface(), os.path.join(self.d, "x.gif"))
        self.assertEqual(os.listdir(self.d), [])

    @unittest.skipUnless(GExiv2 is not None, "GExiv2")
    def test_metadata_kept_orientation_reset(self):
        src = os.path.join(self.d, "photo.jpg")
        subprocess.run(["convert", "-size", "60x40", "xc:green", src], check=True)
        m = GExiv2.Metadata()
        m.open_path(src)
        m.set_tag_string("Exif.Image.Model", "Appareil d'essai")
        m.set_orientation(GExiv2.Orientation.ROT_90)
        m.save_file(src)
        edit.write_surface(surface(), src, replace=True, meta_from=src)
        m = GExiv2.Metadata()
        m.open_path(src)
        self.assertEqual(m.get_tag_string("Exif.Image.Model"), "Appareil d'essai")
        self.assertEqual(m.get_orientation(), GExiv2.Orientation.NORMAL)   # les pixels sont déjà tournés


if __name__ == "__main__":
    unittest.main()
