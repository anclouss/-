"""Real FFmpeg integration coverage, no inference or external network required."""
import math
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave

from app.media import MediaError, _remap_words, _write_ass, extract_audio, probe, render_clip


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class MediaIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="clip-media-test-")
        cls.root = Path(cls.directory.name)
        cls.source = cls.root / "source.mp4"
        wav = cls.root / "tone.wav"
        with wave.open(str(wav), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"".join(struct.pack("<h", 0 if 1 <= i / 16000 < 4 else
                                                   round(5000 * math.sin(2 * math.pi * 440 * i / 16000)))
                                       for i in range(6 * 16000)))
        subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=640x360:rate=15:duration=6", "-i", str(wav),
                        "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        str(cls.source)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_layouts_subtitles_and_audio_export(self):
        words = [{"start": .1, "end": .5, "text": "Привет"},
                 {"start": .55, "end": .9, "text": "{\\pos(0,0)} world"}]
        for layout in ("crop", "fit", "split"):
            with self.subTest(layout=layout):
                output = self.root / f"{layout}.mp4"
                render_clip(self.source, output, 0, 1.2, words,
                            {"layout": layout, "auto_zoom": layout == "crop"})
                info = probe(output)
                self.assertEqual((info["width"], info["height"]), (1080, 1920))
                self.assertEqual(info["video_codec"], "h264")
                self.assertTrue(info["has_audio"])
                self.assertAlmostEqual(info["duration"], 1.2, delta=.1)
        wav = self.root / "extracted.wav"
        extract_audio(self.source, wav)
        with wave.open(str(wav)) as audio:
            self.assertEqual((audio.getnchannels(), audio.getframerate()), (1, 16000))
            self.assertGreater(audio.getnframes(), 90000)

    def test_real_silence_removal_and_subtitle_time_remapping(self):
        words = [{"start": .1, "end": .6, "text": "Before"},
                 {"start": 4.3, "end": 5.3, "text": "After"}]
        output = self.root / "silence-cut.mp4"
        render_clip(self.source, output, 0, 6, words, {"remove_silence": True})
        info = probe(output)
        self.assertGreater(info["duration"], 3)
        self.assertLess(info["duration"], 4)
        remapped = _remap_words(words, 0, [(0, 1.2), (3.8, 6)])
        self.assertAlmostEqual(remapped[1]["start"], 1.7)

    def test_untrusted_ass_tags_and_invalid_source_bounds(self):
        ass = self.root / "safe.ass"
        _write_ass(ass, [{"start": 0, "end": 1, "text": "{\\pos(0,0)}"}], {}, 1)
        self.assertNotIn("{\\pos(0,0)}", ass.read_text(encoding="utf-8-sig"))
        with self.assertRaises(MediaError):
            render_clip(self.source, self.root / "invalid.mp4", -1, 2, [], {})
        with self.assertRaises(MediaError):
            render_clip(self.source, self.root / "invalid.mp4", 0, float("nan"), [], {})

    def test_local_playlist_cannot_read_external_media(self):
        playlist = self.root / "source.m3u8"
        playlist.write_text("#EXTM3U\n#EXTINF:6,\nhttps://example.com/private.ts\n", encoding="utf-8")
        with self.assertRaises(MediaError):
            probe(playlist)
