import json
import subprocess
import unittest
from unittest.mock import patch

import monitor


class MonitorTests(unittest.TestCase):
    def test_profile_uses_https_url_and_canonical_video_url(self):
        payload = json.dumps({
            "id": "1234567890123456789",
            "title": "title",
            "duration": 10,
            "channel_id": "legacy-secuid",
        })
        completed = subprocess.CompletedProcess([], 0, payload + "\n", "")

        with patch.object(monitor.subprocess, "run", return_value=completed) as run:
            videos = monitor.get_video_list(
                "@geograpyh.geniuse",
                channel_id="legacy-secuid",
            )

        command = run.call_args.args[0]
        self.assertIn("https://www.tiktok.com/@geograpyh.geniuse", command)
        self.assertNotIn("tiktokuser:legacy-secuid", command)
        self.assertEqual(
            videos[0]["url"],
            "https://www.tiktok.com/@geograpyh.geniuse/video/1234567890123456789",
        )
        self.assertEqual(command[command.index("--impersonate") + 1], "chrome")

    def test_invalid_yt_dlp_json_raises_profile_error(self):
        completed = subprocess.CompletedProcess([], 0, "not-json\n", "")
        with patch.object(monitor.subprocess, "run", return_value=completed):
            with self.assertRaises(monitor.VideoListError):
                monitor.get_video_list("naa.kana")


if __name__ == "__main__":
    unittest.main()
