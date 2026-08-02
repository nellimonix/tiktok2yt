"""Получение и скачивание видео TikTok через yt-dlp."""

import json
import logging
import os
import subprocess
import sys
from typing import Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)


class VideoListError(RuntimeError):
    pass


def _yt_dlp_command() -> list[str]:
    return [sys.executable, "-m", "yt_dlp"]


def has_audio_stream(video_path: str) -> bool:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_type",
        "-of", "json",
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout or "{}")
        return bool(data.get("streams"))
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return True


def _profile_url(username: str) -> str:
    normalized = username.strip().lstrip("@")
    return f"https://www.tiktok.com/@{quote(normalized, safe='._-')}"


def _video_url(username: str, video_id: str) -> str:
    return f"{_profile_url(username)}/video/{video_id}"


def _run_yt_dlp_list(source_url: str, max_count: int) -> tuple[int, str, str]:
    cmd = _yt_dlp_command() + [
        "--flat-playlist",
        "--playlist-end", str(max_count),
        "--dump-json",
        "--impersonate", "chrome",
        "--extractor-retries", "3",
        "--no-warnings",
        source_url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return result.returncode, result.stdout, result.stderr


def _parse_videos(stdout: str, username: str) -> list[dict]:
    videos = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as error:
            raise VideoListError(f"yt-dlp returned invalid JSON: {error}") from error
        video_id = str(item.get("id") or "").strip()
        if not video_id:
            continue
        duration = item.get("duration") or 0
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            duration = 0
        channel_id = str(item.get("channel_id") or "").strip()
        videos.append({
            "id": video_id,
            "title": str(item.get("title") or "").strip(),
            "duration": duration,
            "url": _video_url(username, video_id),
            "username": username.strip().lstrip("@"),
            "channel_id": channel_id,
        })
    return videos


def get_video_list(
    username: str,
    max_count: int = 10,
    channel_id: Optional[str] = None,
) -> list[dict]:
    normalized = username.strip().lstrip("@")
    source_url = _profile_url(normalized)
    logger.info("Получаем список видео @%s (макс. %s)", normalized, max_count)
    try:
        return_code, stdout, stderr = _run_yt_dlp_list(source_url, max_count)
    except subprocess.TimeoutExpired as error:
        raise VideoListError(
            f"Timeout while fetching TikTok profile @{normalized}"
        ) from error

    if return_code != 0:
        message = stderr.strip() or f"yt-dlp exited with code {return_code}"
        raise VideoListError(f"TikTok profile @{normalized}: {message}")

    videos = _parse_videos(stdout, normalized)
    logger.info("Найдено %s видео у @%s", len(videos), normalized)
    return videos


def _yt_dlp_download(
    video_url: str,
    output_path: str,
    fmt: Optional[str] = None,
    sort: Optional[str] = None,
) -> Optional[str]:
    cmd = _yt_dlp_command() + [
        "-o", output_path,
        "--no-warnings",
        "--merge-output-format", "mp4",
        "--impersonate", "chrome",
        "--extractor-retries", "3",
        "--retries", "3",
    ]
    if fmt:
        cmd.extend(["-f", fmt])
    if sort:
        cmd.extend(["-S", sort])
    cmd.append(video_url)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            logger.error("Ошибка скачивания: %s", result.stderr.strip())
            return None
    except subprocess.TimeoutExpired:
        logger.error("Таймаут при скачивании: %s", video_url)
        return None

    candidates = [output_path]
    base = os.path.splitext(output_path)[0]
    for extension in [".mp4", ".webm", ".mkv"]:
        candidates.append(output_path + extension)
        candidates.append(base + extension)
    for path in candidates:
        if os.path.exists(path):
            return path

    logger.error("Файл не найден после скачивания: %s", output_path)
    return None


def _cleanup_partial(output_path: str) -> None:
    base = os.path.splitext(output_path)[0]
    paths = [output_path] + [
        base + extension for extension in [".mp4", ".webm", ".mkv", ".part"]
    ]
    for path in paths:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def download_video(video_url: str, output_path: str) -> Optional[str]:
    attempts = [
        {"sort": "vcodec:h264", "label": "h264 prefer"},
        {"fmt": "bv*+ba/b", "label": "video+audio merge"},
    ]

    for attempt in attempts:
        logger.info("Скачиваем (%s): %s", attempt["label"], video_url)
        path = _yt_dlp_download(
            video_url,
            output_path,
            fmt=attempt.get("fmt"),
            sort=attempt.get("sort"),
        )
        if not path:
            _cleanup_partial(output_path)
            continue
        if has_audio_stream(path):
            logger.info("Скачано: %s", path)
            return path
        logger.warning("Скачано без аудио: %s", path)
        _cleanup_partial(output_path)

    logger.error("Не удалось получить видео с аудио: %s", video_url)
    return None
