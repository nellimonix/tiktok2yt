import json
import io
import logging
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


def _install_dependency_stubs():
    requests = types.ModuleType("requests")
    requests.post = lambda *args, **kwargs: None
    requests.exceptions = types.SimpleNamespace(RequestException=Exception)
    sys.modules.setdefault("requests", requests)

    modules = {
        "google": types.ModuleType("google"),
        "google.oauth2": types.ModuleType("google.oauth2"),
        "google.oauth2.credentials": types.ModuleType("google.oauth2.credentials"),
        "google_auth_oauthlib": types.ModuleType("google_auth_oauthlib"),
        "google_auth_oauthlib.flow": types.ModuleType("google_auth_oauthlib.flow"),
        "google.auth": types.ModuleType("google.auth"),
        "google.auth.transport": types.ModuleType("google.auth.transport"),
        "google.auth.transport.requests": types.ModuleType("google.auth.transport.requests"),
        "googleapiclient": types.ModuleType("googleapiclient"),
        "googleapiclient.discovery": types.ModuleType("googleapiclient.discovery"),
        "googleapiclient.http": types.ModuleType("googleapiclient.http"),
        "googleapiclient.errors": types.ModuleType("googleapiclient.errors"),
    }
    modules["google.oauth2.credentials"].Credentials = object
    modules["google_auth_oauthlib.flow"].InstalledAppFlow = object
    modules["google.auth.transport.requests"].Request = object
    modules["googleapiclient.discovery"].build = lambda *args, **kwargs: None
    modules["googleapiclient.http"].MediaFileUpload = object
    modules["googleapiclient.errors"].HttpError = type("HttpError", (Exception,), {})
    for name, module in modules.items():
        sys.modules.setdefault(name, module)


_install_dependency_stubs()

import main
import state_manager


class PipelineStateTests(unittest.TestCase):
    def setUp(self):
        self.previous_cwd = os.getcwd()
        self.temp_directory = tempfile.TemporaryDirectory()
        os.chdir(self.temp_directory.name)
        self.work_dir = Path("tmp")
        self.config = {
            "temp_dir": str(self.work_dir),
            "api_key": "test-key",
            "api_url": "https://example.invalid",
            "tiktok_accounts": ["account"],
        }
        self.video = {
            "id": "1234567890123456789",
            "url": "https://www.tiktok.com/@account/video/1234567890123456789",
            "username": "account",
            "title": "Test title",
            "duration": 12,
        }
        self.state = state_manager.load_state()

    def tearDown(self):
        logging.shutdown()
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
            handler.close()
        os.chdir(self.previous_cwd)
        self.temp_directory.cleanup()

    @staticmethod
    def _create_file(path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")
        return str(path)

    def _download_success(self, _url, output_path):
        return self._create_file(output_path)

    def _processing_success(self, **kwargs):
        return self._create_file(kwargs["output_path"])

    def _success_patches(self):
        return (
            patch.object(main, "download_video", side_effect=self._download_success),
            patch.object(main, "process_video", side_effect=self._processing_success),
            patch.object(main, "transcribe", return_value="spoken text"),
            patch.object(main, "generate_title", return_value="Generated title"),
            patch.object(main, "upload_video", return_value="abcDEF123_-"),
        )

    def test_download_failure_is_not_processed(self):
        with patch.object(main, "download_video", return_value=None):
            result = main.process_single_video(
                self.video, self.config, object(), self.state
            )

        self.assertFalse(result)
        self.assertNotIn(self.video["id"], self.state["processed_videos"])
        self.assertEqual(self.state["failed_videos"][0]["stage"], main.STAGE_DOWNLOAD)

    def test_processing_failure_preserves_raw_file(self):
        with patch.object(main, "download_video", side_effect=self._download_success), patch.object(
            main, "process_video", return_value=None
        ):
            result = main.process_single_video(
                self.video, self.config, object(), self.state
            )

        failure = self.state["failed_videos"][0]
        self.assertFalse(result)
        self.assertEqual(failure["stage"], main.STAGE_PROCESSING)
        self.assertTrue(Path(failure["local_file_path"]).exists())

    def test_youtube_failure_preserves_processed_file(self):
        with patch.object(main, "download_video", side_effect=self._download_success), patch.object(
            main, "process_video", side_effect=self._processing_success
        ), patch.object(main, "transcribe", return_value="spoken text"), patch.object(
            main, "generate_title", return_value="Generated title"
        ), patch.object(main, "upload_video", return_value=None):
            result = main.process_single_video(
                self.video, self.config, object(), self.state
            )

        failure = self.state["failed_videos"][0]
        self.assertFalse(result)
        self.assertEqual(failure["stage"], main.STAGE_UPLOAD)
        self.assertTrue(Path(failure["local_file_path"]).exists())

    def test_repeated_failure_updates_one_record(self):
        with patch.object(main, "download_video", return_value=None):
            main.process_single_video(self.video, self.config, object(), self.state)
            main.process_single_video(self.video, self.config, object(), self.state)

        self.assertEqual(len(self.state["failed_videos"]), 1)
        self.assertEqual(self.state["failed_videos"][0]["attempts"], 2)

    def test_retry_failed_succeeds_and_removes_failure(self):
        state_manager.record_failure(
            self.state,
            self.video,
            main.STAGE_DOWNLOAD,
            "download failed",
        )
        download, processing, transcription, title, upload = self._success_patches()
        with download, processing, transcription, title, upload:
            successful, remaining = main.retry_failed(
                self.config, self.state, object()
            )

        self.assertEqual((successful, remaining), (1, 0))
        self.assertEqual(self.state["processed_videos"], [self.video["id"]])
        self.assertEqual(self.state["failed_videos"], [])
        self.assertEqual(
            self.state["video_states"][self.video["id"]]["youtube_id"],
            "abcDEF123_-",
        )

    def test_retry_failed_cli_exits_without_monitoring(self):
        Path("config.json").write_text(json.dumps({
            **self.config,
            "youtube_client_secret": "client_secret.json",
            "log_file": "bot.log",
        }), encoding="utf-8")
        state_manager.record_failure(
            self.state,
            self.video,
            main.STAGE_DOWNLOAD,
            "download failed",
        )
        download, processing, transcription, title, upload = self._success_patches()
        with download, processing, transcription, title, upload, patch.object(
            main, "get_youtube_service", return_value=object()
        ), patch.object(main, "monitoring_loop") as monitoring:
            exit_code = main.main(["--retry-failed"])

        self.assertEqual(exit_code, 0)
        monitoring.assert_not_called()
        self.assertEqual(state_manager.load_state()["failed_videos"], [])

    def test_second_retry_after_success_does_not_upload_again(self):
        state_manager.record_failure(
            self.state,
            self.video,
            main.STAGE_DOWNLOAD,
            "download failed",
        )
        download, processing, transcription, title, upload = self._success_patches()
        with download, processing, transcription, title, upload as upload_mock:
            main.retry_failed(self.config, self.state, object())
            successful, remaining = main.retry_failed(
                self.config, self.state, object()
            )

        self.assertEqual((successful, remaining), (0, 0))
        self.assertEqual(upload_mock.call_count, 1)
        self.assertEqual(self.state["processed_videos"].count(self.video["id"]), 1)

    def test_atomic_replace_failure_preserves_previous_state(self):
        state_manager.save_state(self.state)
        previous = Path("state.json").read_text(encoding="utf-8")
        self.state["first_run_done"] = True

        with patch.object(state_manager.os, "replace", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                state_manager.save_state(self.state)

        self.assertEqual(Path("state.json").read_text(encoding="utf-8"), previous)
        self.assertEqual(list(Path(".").glob("state.json.tmp.*")), [])

    def test_legacy_state_is_backed_up_before_migration(self):
        legacy = {
            "first_run_done": True,
            "processed_videos": ["123"],
            "scheduled_queue": [],
            "last_check": {},
            "channel_ids": {},
        }
        Path("state.json").write_text(json.dumps(legacy), encoding="utf-8")

        migrated = state_manager.load_state()

        backups = list(Path("state_backups").glob("state-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8")), legacy)
        self.assertEqual(migrated["failed_videos"], [])
        self.assertEqual(migrated["video_states"], {})
        persisted = json.loads(Path("state.json").read_text(encoding="utf-8"))
        self.assertIn("failed_videos", persisted)

    def test_corrupted_state_stops_instead_of_reuploading(self):
        Path("state.json").write_text("{broken", encoding="utf-8")

        with self.assertRaises(RuntimeError):
            state_manager.load_state()

    def test_interrupted_processing_becomes_failure(self):
        state_manager.mark_found(self.state, self.video)
        raw_path = self._create_file(self.work_dir / "interrupted_raw.mp4")
        state_manager.mark_stage(
            self.state,
            self.video,
            main.STAGE_PROCESSING,
            raw_path,
        )

        restored = state_manager.load_state()
        count = state_manager.recover_interrupted(restored)

        self.assertEqual(count, 1)
        self.assertEqual(restored["failed_videos"][0]["stage"], main.STAGE_PROCESSING)
        self.assertEqual(
            restored["video_states"][self.video["id"]]["status"],
            state_manager.STATUS_ERROR,
        )
        self.assertEqual(restored["failed_videos"][0]["local_file_path"], raw_path)

    def test_profile_error_does_not_finish_first_run(self):
        with patch.object(main, "get_video_list", side_effect=main.VideoListError("failed")):
            main.first_run(self.config, self.state, object())

        self.assertFalse(self.state["first_run_done"])
        self.assertEqual(self.state["processed_videos"], [])

    def test_list_failed_does_not_authorize_youtube(self):
        Path("config.json").write_text(json.dumps({
            **self.config,
            "youtube_client_secret": "client_secret.json",
            "log_file": "bot.log",
        }), encoding="utf-8")
        state_manager.record_failure(
            self.state,
            self.video,
            main.STAGE_DOWNLOAD,
            "download failed",
        )
        output = io.StringIO()
        with patch.object(main, "get_youtube_service") as authorize, redirect_stdout(output):
            exit_code = main.main(["--list-failed"])

        self.assertEqual(exit_code, 0)
        authorize.assert_not_called()
        self.assertIn(self.video["id"], output.getvalue())


if __name__ == "__main__":
    unittest.main()
