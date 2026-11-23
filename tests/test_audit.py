"""Audit de novembre 2026 : noms piégés, liens symboliques, réglages. Chaque cas échouait sur l'ancien code."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from argos.backend import edit, imagetools  # noqa: E402
from argos.settings import Settings  # noqa: E402


def dims(path):
    out = subprocess.run(["identify", "-format", "%w %h", "fd:0"], stdin=open(path, "rb"), capture_output=True,
                         text=True)
    return tuple(int(v) for v in out.stdout.split())


def photo(path, size="60x40", ext=".jpg"):
    """Photo créée sous un nom sûr ailleurs puis déplacée : convert lui-même lirait les « % » du nom comme un gabarit."""
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(tempfile.mkdtemp(), "fabrique" + ext)
    subprocess.run(["convert", "-size", size, "xc:green", tmp], check=True)
    shutil.move(tmp, path)
    os.rmdir(os.path.dirname(tmp))
    return path


@unittest.skipUnless(edit.magick_command(), "ImageMagick")
class AuditTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_percent_names_rotate_and_tools(self):
        """« 50%off.jpg », dossier « Taux 5%o » : ImageMagick lit « %o », « %d »… comme un gabarit — pivoter,
        recadrer et « Convertir / redimensionner » répondaient « no images defined »."""
        for p in (os.path.join(self.d, "n", "50%off.jpg"), os.path.join(self.d, "Taux 5%o", "a.png"),
                  os.path.join(self.d, "n", "p%d.jpg")):
            photo(p, ext=os.path.splitext(p)[1])
            edit.apply(p, p, rotation=1, replace=True)
            self.assertEqual(dims(p), (40, 60), p)
            out = edit.apply(p, p, crop=(0, 0, 20, 30))
            self.assertEqual(dims(out), (20, 30), p)
            res = imagetools.process(imagetools.Job(), [p], imagetools.Options(resize="scale", scale=50, suffix="-s"))
            self.assertEqual(res.errors, [], p)
            self.assertFalse([n for n in os.listdir(os.path.dirname(p)) if n.startswith(".argos")])

    def test_save_through_symlink_keeps_link(self):
        """Photo ouverte par un lien symbolique : enregistrer remplaçait le LIEN par un fichier (la vraie photo
        restait telle quelle) ; « Annuler » de même."""
        real = photo(os.path.join(self.d, "vraies", "r.jpg"))
        link = os.path.join(self.d, "lien.jpg")
        os.symlink(real, link)
        copy = edit.backup(link)
        edit.apply(link, link, rotation=1, replace=True)
        self.assertTrue(os.path.islink(link))
        self.assertEqual(dims(real), (40, 60))
        edit.restore(copy, link)
        self.assertTrue(os.path.islink(link))
        self.assertEqual(dims(real), (60, 40))

    def test_settings_non_utf8_folder(self):
        """Dernier dossier au nom non UTF-8 : save() levait UnicodeEncodeError (plus rien d'enregistré)."""
        s = Settings(os.path.join(self.d, "settings.json"))
        weird = os.fsdecode(os.path.join(os.fsencode(self.d), b"caf\xe9"))
        s["last_folder"] = weird
        self.assertEqual(Settings(s.path)["last_folder"], weird)


if __name__ == "__main__":
    unittest.main()
