import unittest
from unittest.mock import patch

import video_processor


class VideoProcessorTests(unittest.TestCase):
    def test_filter_normalizes_video_to_vertical_shorts(self):
        video_filter = video_processor.build_shorts_filter(
            overlay_text="Лимонный чай",
            font_path="/tmp/font.ttf",
            font_size=52,
            opacity=0.5,
        )

        self.assertIn("scale=1080:1920:force_original_aspect_ratio=increase", video_filter)
        self.assertIn("crop=1080:1920", video_filter)
        self.assertIn("scale=1080:1920:force_original_aspect_ratio=decrease", video_filter)
        self.assertIn("text='Лимонный чай'", video_filter)
        self.assertIn("fontsize=110", video_filter)
        self.assertIn("y=(h-text_h)*0.75", video_filter)
        self.assertIn("format=yuv420p,setsar=1[video]", video_filter)

    def test_watermark_text_is_escaped_for_ffmpeg(self):
        video_filter = video_processor.build_shorts_filter(
            overlay_text="Лимонный: чай 100%",
            font_path="C:/font.ttf",
            font_size=52,
            opacity=0.5,
        )

        self.assertIn(r"Лимонный\: чай 100\%", video_filter)
        self.assertIn("fontfile='C\\:/font.ttf'", video_filter)

    @patch.object(video_processor.os.path, "exists", return_value=True)
    @patch.object(video_processor.os.path, "getsize", return_value=1024)
    @patch.object(video_processor, "find_font", return_value="/tmp/font.ttf")
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

        result = video_processor.process_video("input.mp4", "output.mp4")

        self.assertEqual(result, "output.mp4")
        command = run.call_args.args[0]
        self.assertIn("-filter_complex", command)
        self.assertIn("[video]", command)
        self.assertIn("0:a?", command)


if __name__ == "__main__":
    unittest.main()
