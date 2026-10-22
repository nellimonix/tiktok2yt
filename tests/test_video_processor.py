import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import video_processor


class VideoProcessorTests(unittest.TestCase):
    def test_filter_normalizes_video_to_vertical_shorts(self):
        video_filter = video_processor.build_shorts_filter()

        self.assertIn("scale=270:480:force_original_aspect_ratio=increase", video_filter)
        self.assertIn("crop=270:480,boxblur=5:1,scale=1080:1920", video_filter)
        self.assertIn("scale=1080:1920:force_original_aspect_ratio=decrease", video_filter)
        self.assertNotIn("drawtext", video_filter)
        self.assertIn("[composed][1:v]overlay=x=(W-w)/2:y=(H-h)*0.75", video_filter)
        self.assertIn("format=yuv420p,setsar=1[video]", video_filter)

    def test_vertical_source_skips_background_rendering(self):
        for width, height in [(720, 1280), (1080, 1920), (360, 640)]:
            with self.subTest(width=width, height=height):
                video_filter = video_processor.build_shorts_filter(
                    source_width=width, source_height=height,
                )
                self.assertNotIn("split=", video_filter)
                self.assertNotIn("boxblur", video_filter)
                self.assertIn("scale=1080:1920", video_filter)
                self.assertIn("[composed][1:v]overlay", video_filter)

    def test_watermark_fits_output_with_padding(self):
        font_path = str(Path(video_processor.__file__).parent / "font" / "14076.ttf")
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "watermark.png")
            for width, height in [(1080, 1920), (360, 640), (720, 1280)]:
                for text in ["Лимонный чай", "Лимонный: чай 100%, 'канал'", "Лимонный чай " * 5]:
                    with self.subTest(width=width, text=text):
                        video_processor.render_watermark(
                            output_path, text, font_path, 110, 0.5, width, height,
                        )
                        with Image.open(output_path) as watermark:
                            self.assertEqual(watermark.mode, "RGBA")
                            self.assertLessEqual(watermark.width, round(width * 0.8))
                            self.assertLessEqual(watermark.height, round(height * 0.15))
                            ink = watermark.convert("RGB").getbbox()
                            self.assertIsNotNone(ink)
                            self.assertGreaterEqual(ink[0], 12)
                            self.assertGreaterEqual(ink[1], 12)
                            self.assertLessEqual(ink[2], watermark.width - 12)
                            self.assertLessEqual(ink[3], watermark.height - 12)

    @patch.object(video_processor.os.path, "exists", return_value=True)
    @patch.object(video_processor.os.path, "getsize", return_value=1024)
    @patch.object(video_processor, "find_font")
    @patch.object(video_processor, "get_video_dimensions", return_value=(1120, 720))
    @patch.object(video_processor, "get_video_duration", return_value=15)
    @patch.object(video_processor.subprocess, "run")
    def test_process_maps_filtered_video_and_optional_audio(
        self,
        run,
        _duration,
        _dimensions,
        _font,
        _getsize,
        _exists,
    ):
        run.return_value.returncode = 0
        _font.return_value = str(Path(video_processor.__file__).parent / "font" / "14076.ttf")

        result = video_processor.process_video("input.mp4", "output.mp4")

        self.assertEqual(result, "output.mp4")
        command = run.call_args.args[0]
        self.assertIn("-filter_complex", command)
        self.assertIn("[video]", command)
        self.assertIn("0:a?", command)
        input_positions = [index for index, value in enumerate(command) if value == "-i"]
        self.assertEqual(command[input_positions[0] + 1], "input.mp4")
        watermark_path = command[input_positions[1] + 1]
        self.assertTrue(watermark_path.endswith("overlay.png"))
        self.assertFalse(Path(watermark_path).exists())
        self.assertEqual(command[command.index("-filter_complex_threads") + 1], "1")
        self.assertEqual(command[command.index("-threads:v") + 1], "1")
        self.assertEqual(command[command.index("-threads:a") + 1], "1")
        self.assertEqual(command[command.index("-preset") + 1], "veryfast")
        for index in input_positions:
            self.assertEqual(command[index - 2:index], ["-threads", "1"])

    @patch.object(video_processor, "get_video_dimensions", return_value=(720, 1280))
    @patch.object(video_processor, "get_video_duration", return_value=15)
    @patch.object(video_processor, "render_watermark", side_effect=OSError("font error"))
    @patch.object(video_processor.subprocess, "run")
    def test_watermark_failure_does_not_encode_video(self, run, _render, _duration, _dimensions):
        self.assertIsNone(video_processor.process_video("input.mp4", "output.mp4"))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
