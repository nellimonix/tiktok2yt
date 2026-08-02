#!/usr/bin/env python3
"""TikTok → YouTube Shorts."""

import argparse
import json
import logging
import os
import re
import sys
import time

from ai_title import generate_title
from monitor import VideoListError, download_video, get_video_list
from state_manager import (
    get_channel_id,
    is_first_run,
    is_processed,
    load_state,
    mark_first_run_done,
    mark_found,
    mark_stage,
    mark_uploaded,
    record_failure,
    recover_interrupted,
    remove_failure,
    set_channel_id,
)
from transcriber import transcribe
from uploader import (
    calculate_schedule_times,
    get_youtube_service,
    is_valid_youtube_video_id,
    upload_video,
)
from video_processor import process_video

CONFIG_FILE = "config.json"

STAGE_DISCOVERY = "discovery"
STAGE_DOWNLOAD = "download"
STAGE_PROCESSING = "processing"
STAGE_TRANSCRIPTION = "transcription"
STAGE_TITLE = "title_generation"
STAGE_UPLOAD = "youtube_upload"

logger = logging.getLogger(__name__)


class PipelineError(RuntimeError):
    def __init__(self, stage: str, message: str, local_file_path: str = ""):
        super().__init__(message)
        self.stage = stage
        self.local_file_path = local_file_path


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(
            f"{CONFIG_FILE} не найден. Скопируйте config.example.json и заполните его."
        )
    with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
        return json.load(handle)


def setup_logging(log_file: str) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )


def _clean_fallback_title(raw: str, default: str = "Интересное видео") -> str:
    if not raw:
        return default
    cleaned = re.sub(r"#\S+", "", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—_.,:;|")
    if len(cleaned) < 3:
        return default
    return cleaned[:60]


def _existing_retry_file(failure: dict, stage: str) -> tuple[str, str]:
    saved_path = failure.get("local_file_path", "") if failure else ""
    if not saved_path or not os.path.exists(saved_path):
        return "", ""
    if stage in {STAGE_TRANSCRIPTION, STAGE_TITLE, STAGE_UPLOAD}:
        return "", saved_path
    return saved_path, ""


def _cleanup_success_files(video_id: str, tmp_dir: str, paths: set[str]) -> None:
    base = os.path.join(tmp_dir, f"{video_id}_raw")
    paths.update(base + extension for extension in [".mp4", ".webm", ".mkv", ".part"])
    for path in paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError as error:
                logger.warning("Не удалось удалить временный файл %s: %s", path, error)


def process_single_video(
    video_info: dict,
    config: dict,
    youtube_service,
    state: dict,
    publish_at=None,
    retry_failure: dict | None = None,
) -> bool:
    video_id = str(video_info["id"])
    if is_processed(state, video_id):
        remove_failure(state, video_id)
        return True

    tmp_dir = config.get("temp_dir", "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    raw_target = os.path.join(tmp_dir, f"{video_id}_raw.mp4")
    processed_target = os.path.join(tmp_dir, f"{video_id}_final.mp4")
    retry_stage = retry_failure.get("stage", "") if retry_failure else ""
    raw_path, processed_path = _existing_retry_file(retry_failure or {}, retry_stage)
    current_stage = STAGE_DOWNLOAD

    mark_found(state, video_info)
    logger.info("Обработка видео %s (@%s)", video_id, video_info.get("username", "?"))

    try:
        if not raw_path and not processed_path:
            current_stage = STAGE_DOWNLOAD
            mark_stage(state, video_info, current_stage)
            raw_path = download_video(video_info["url"], raw_target)
            if not raw_path:
                raise PipelineError(current_stage, "yt-dlp did not download the video")

        if not processed_path:
            current_stage = STAGE_PROCESSING
            mark_stage(state, video_info, current_stage, raw_path)
            processed_path = process_video(
                input_path=raw_path,
                output_path=processed_target,
                overlay_text=config.get("overlay_text", "Лимонный чай"),
                font_size=config.get("overlay_font_size", 52),
                opacity=config.get("overlay_opacity", 0.5),
                max_duration=config.get("max_duration_seconds", 179),
            )
            if not processed_path:
                raise PipelineError(
                    current_stage,
                    "ffmpeg did not produce the processed video",
                    raw_path,
                )

        current_stage = STAGE_TRANSCRIPTION
        mark_stage(state, video_info, current_stage, processed_path)
        transcript = transcribe(
            processed_path,
            model_size=config.get("whisper_model", "base"),
        )
        if transcript is None:
            raise PipelineError(
                current_stage,
                "Transcription failed",
                processed_path,
            )

        current_stage = STAGE_TITLE
        mark_stage(state, video_info, current_stage, processed_path)
        title = generate_title(
            transcript=transcript,
            api_key=config["api_key"],
            api_url=config.get("api_url"),
            model=config.get("model"),
            fallback_title=_clean_fallback_title(video_info.get("title", "")),
            raise_on_failure=True,
        )

        current_stage = STAGE_UPLOAD
        mark_stage(state, video_info, current_stage, processed_path)
        youtube_id = upload_video(
            youtube_service=youtube_service,
            video_path=processed_path,
            title=title,
            description="#shorts",
            category_id=config.get("youtube_category_id", "1"),
            publish_at=publish_at,
            tags=["shorts"],
        )
        if not is_valid_youtube_video_id(youtube_id):
            raise PipelineError(
                current_stage,
                "YouTube did not return a valid video ID",
                processed_path,
            )

        mark_uploaded(state, video_info, youtube_id)
        _cleanup_success_files(
            video_id,
            tmp_dir,
            {raw_path, processed_path, raw_target, processed_target},
        )
        logger.info("УСПЕХ! TikTok ID: %s, YouTube ID: %s", video_id, youtube_id)
        return True

    except PipelineError as error:
        local_path = error.local_file_path
        if not local_path:
            local_path = processed_path if processed_path and os.path.exists(processed_path) else raw_path
        record_failure(
            state,
            video_info,
            error.stage,
            str(error),
            os.path.normpath(local_path) if local_path else "",
        )
        logger.error("Видео %s: этап %s: %s", video_id, error.stage, error)
        return False
    except Exception as error:
        local_path = processed_path if processed_path and os.path.exists(processed_path) else raw_path
        record_failure(
            state,
            video_info,
            current_stage,
            f"{type(error).__name__}: {error}",
            os.path.normpath(local_path) if local_path else "",
        )
        logger.exception("Видео %s: этап %s", video_id, current_stage)
        return False


def _fetch_account_videos(username: str, count: int, state: dict) -> list[dict]:
    videos = get_video_list(
        username,
        max_count=count,
        channel_id=get_channel_id(state, username),
    )
    for video in videos:
        channel_id = video.get("channel_id")
        if channel_id:
            set_channel_id(state, username, channel_id)
            break
    return videos


def first_run(config: dict, state: dict, youtube_service) -> None:
    accounts = config["tiktok_accounts"]
    per_account = config.get("initial_videos_per_account", 5)
    all_videos = []
    profile_error = False

    for username in accounts:
        try:
            videos = _fetch_account_videos(username, per_account, state)
        except VideoListError as error:
            profile_error = True
            logger.error("Ошибка обнаружения видео @%s: %s", username, error)
            continue
        all_videos.extend(
            video for video in videos
            if not is_processed(state, video["id"])
        )

    schedule = calculate_schedule_times(
        count=len(all_videos),
        interval_hours=config.get("schedule_interval_hours", 2),
        start_from_now=True,
    )
    for index, (video, publish_at) in enumerate(zip(all_videos, schedule)):
        process_single_video(video, config, youtube_service, state, publish_at)
        if index < len(all_videos) - 1:
            time.sleep(5)

    if not profile_error:
        mark_first_run_done(state)


def monitoring_loop(config: dict, state: dict, youtube_service) -> None:
    interval = config.get("check_interval_minutes", 5) * 60
    accounts = config["tiktok_accounts"]
    logger.info("Запущен мониторинг %s TikTok-аккаунтов", len(accounts))

    while True:
        try:
            for username in accounts:
                try:
                    videos = _fetch_account_videos(username, 5, state)
                except VideoListError as error:
                    logger.error("Ошибка обнаружения видео @%s: %s", username, error)
                    continue

                attempted = 0
                for video in videos:
                    if is_processed(state, video["id"]):
                        continue
                    attempted += 1
                    process_single_video(video, config, youtube_service, state)
                    time.sleep(3)
                if attempted == 0:
                    logger.info("@%s: нет новых видео", username)
        except KeyboardInterrupt:
            logger.info("Остановка по Ctrl+C")
            break
        except Exception:
            logger.exception("Ошибка в цикле мониторинга")

        logger.info("Следующая проверка через %s мин", interval // 60)
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Остановка по Ctrl+C")
            break


def list_failed(state: dict) -> int:
    failures = state.get("failed_videos", [])
    if not failures:
        print("Ошибочных видео нет.")
        return 0
    print(f"Ошибочных видео: {len(failures)}")
    for failure in failures:
        print(
            f"{failure.get('tiktok_id')} | @{failure.get('account')} | "
            f"{failure.get('stage')} | попыток: {failure.get('attempts')} | "
            f"последняя: {failure.get('last_attempt_at')}"
        )
        print(f"  URL: {failure.get('url')}")
        print(f"  Ошибка: {failure.get('error')}")
        if failure.get("local_file_path"):
            print(f"  Файл: {failure.get('local_file_path')}")
    return len(failures)


def retry_failed(config: dict, state: dict, youtube_service) -> tuple[int, int]:
    failures = [failure.copy() for failure in state.get("failed_videos", [])]
    successful = 0
    for failure in failures:
        video_id = str(failure["tiktok_id"])
        if is_processed(state, video_id):
            remove_failure(state, video_id)
            continue
        video = {
            "id": video_id,
            "url": failure["url"],
            "username": failure["account"],
            "title": failure.get("title", ""),
            "duration": failure.get("duration", 0),
        }
        if process_single_video(
            video,
            config,
            youtube_service,
            state,
            retry_failure=failure,
        ):
            successful += 1
    remaining = len(state.get("failed_videos", []))
    logger.info("Повторная обработка: успешно %s, осталось ошибок %s", successful, remaining)
    return successful, remaining


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="TikTok → YouTube Shorts")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--retry-failed", action="store_true")
    modes.add_argument("--list-failed", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    config = load_config()
    setup_logging(config.get("log_file", "bot.log"))
    state = load_state()
    interrupted = recover_interrupted(state)
    if interrupted:
        logger.warning("Восстановлено прерванных обработок: %s", interrupted)

    if args.list_failed:
        list_failed(state)
        return 0

    tmp_dir = config.get("temp_dir", "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    youtube_service = get_youtube_service(config["youtube_client_secret"])

    if args.retry_failed:
        _, remaining = retry_failed(config, state, youtube_service)
        return 1 if remaining else 0

    if is_first_run(state):
        first_run(config, state, youtube_service)
    monitoring_loop(config, state, youtube_service)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
