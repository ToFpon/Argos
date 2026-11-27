"""Vidéos (demande de Tof, comme gThumb) : lecture du dossier et informations par ffprobe."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from argos.backend import folder, loader  # noqa: E402


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg")
class VideoTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        subprocess.run(["convert", "-size", "40x30", "xc:red", os.path.join(self.d, "a.jpg")], check=True)
        self.clip = os.path.join(self.d, "b.webm")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10",
                        "-f", "lavfi", "-i", "sine", "-t", "2", "-c:v", "libvpx-vp9", "-c:a", "libopus", self.clip],
                       check=True)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_scan_with_and_without_videos(self):
        self.assertEqual([e.name for e in folder.scan(self.d)[1]], ["a.jpg"])
        self.assertEqual([e.name for e in folder.scan(self.d, videos=True)[1]], ["a.jpg", "b.webm"])

    def test_probe(self):
        pic = loader.load(self.clip)
        self.assertTrue(pic.video and not pic.error and pic.texture is None)
        self.assertEqual((pic.width, pic.height, pic.format), (160, 90, "WEBM"))
        self.assertAlmostEqual(pic.duration, 2.0, delta=0.2)
        self.assertEqual(pic.codecs, ("VP9", "Opus"))


if __name__ == "__main__":
    unittest.main()
