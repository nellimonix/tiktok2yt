"""
Модуль мониторинга TikTok аккаунтов.
Получает список видео, скачивает новые.
"""

import json
import logging
import os
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


def has_audio_stream(video_path: str) -> bool:
    """Проверяет наличие хотя бы одной аудиодорожки через ffprobe."""
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
        return True  # неизвестно — пусть дальнейшая логика решает


_SEC_UID_ERROR = "Unable to extract secondary user ID"


def _run_yt_dlp_list(source_url: str, max_count: int) -> tuple[int, str, str]:
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--playlist-end", str(max_count),
        "--print", "%(id)s|||%(title)s|||%(duration)s|||%(webpage_url)s|||%(channel_id)s",
        "--no-warnings",
        source_url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return result.returncode, result.stdout, result.stderr


def _parse_videos(stdout: str, username: str) -> list[dict]:
    videos = []
    for line in stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("|||")
        if len(parts) < 4:
            continue
        vid_id, title, duration_str, vid_url = parts[:4]
        channel_id = parts[4].strip() if len(parts) >= 5 else ""
        try:
            duration = float(duration_str) if duration_str != "NA" else 0
        except ValueError:
            duration = 0
        videos.append({
            "id": vid_id.strip(),
            "title": title.strip(),
            "duration": duration,
            "url": vid_url.strip(),
            "username": username,
            "channel_id": channel_id if channel_id and channel_id != "NA" else "",
        })
    return videos


def get_video_list(
    username: str,
    max_count: int = 10,
    channel_id: Optional[str] = None,
) -> list[dict]:
    """
    Получает список последних видео аккаунта.
    Если задан channel_id (secUid), использует форму tiktokuser:<secUid> —
    обход бага yt-dlp "Unable to extract secondary user ID" на profile URL.
    Возвращает список словарей: {id, url, title, duration, username, channel_id}
    """
    logger.info(f"Получаем список видео @{username} (макс. {max_count})")

    attempts: list[tuple[str, str]] = []
    if channel_id:
        attempts.append((f"tiktokuser:{channel_id}", "channel_id"))
    attempts.append((f"https://www.tiktok.com/@{username}", "username"))

    last_stderr = ""
    for source_url, mode in attempts:
        try:
            rc, stdout, stderr = _run_yt_dlp_list(source_url, max_count)
        except subprocess.TimeoutExpired:
            logger.error(f"Таймаут при получении списка @{username} ({mode})")
            continue

        if rc == 0:
            videos = _parse_videos(stdout, username)
            logger.info(f"Найдено {len(videos)} видео у @{username} ({mode})")
            return videos

        last_stderr = stderr
        if mode == "username" and _SEC_UID_ERROR in stderr:
            logger.warning(
                f"yt-dlp не смог извлечь secUid для @{username} по profile URL. "
                f"Добавьте channel_id в state.json[\"channel_ids\"][\"{username}\"] "
                f"для использования формы tiktokuser:<secUid>."
            )
        else:
            logger.error(f"yt-dlp ошибка для @{username} ({mode}): {stderr}")

    if last_stderr and _SEC_UID_ERROR not in last_stderr:
        logger.error(f"Не удалось получить список @{username}: {last_stderr}")
    return []


def _yt_dlp_download(
    video_url: str,
    output_path: str,
    fmt: Optional[str] = None,
    sort: Optional[str] = None,
) -> Optional[str]:
    cmd = [
        "yt-dlp",
        "-o", output_path,
        "--no-warnings",
        "--merge-output-format", "mp4",
    ]
    if fmt:
        cmd.extend(["-f", fmt])
    if sort:
        cmd.extend(["-S", sort])
    cmd.append(video_url)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            logger.error(f"Ошибка скачивания: {result.stderr}")
            return None
    except subprocess.TimeoutExpired:
        logger.error(f"Таймаут при скачивании: {video_url}")
        return None

    candidates = [output_path]
    base = os.path.splitext(output_path)[0]
    for ext in [".mp4", ".webm", ".mkv"]:
        candidates.append(output_path + ext)
        candidates.append(base + ext)
    for path in candidates:
        if os.path.exists(path):
            return path

    logger.error(f"Файл не найден после скачивания: {output_path}")
    return None


def _cleanup_partial(output_path: str) -> None:
    base = os.path.splitext(output_path)[0]
    for path in [output_path] + [base + ext for ext in [".mp4", ".webm", ".mkv", ".part"]]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def download_video(video_url: str, output_path: str) -> Optional[str]:
    """
    Скачивает видео без водяного знака TikTok.

    TikTok отдаёт h265 (bytevc1) потоки видео-only, но yt-dlp ошибочно метит их
    acodec=aac. Поэтому без явного предпочтения h264 -f best может выбрать h265
    и файл будет без звука. Сначала пробуем sort vcodec:h264, потом форс-merge.
    """
    attempts = [
        {"sort": "vcodec:h264", "label": "первая попытка (h264 prefer)"},
        {"fmt": "bv*+ba/b", "label": "повтор с принудительным merge видео+аудио"},
    ]

    for attempt in attempts:
        logger.info(f"Скачиваем ({attempt['label']}): {video_url}")
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
            logger.info(f"Скачано: {path}")
            return path

        logger.warning(f"Скачано без аудио: {path} — пробуем другой формат")
        _cleanup_partial(output_path)

    logger.error(f"Не удалось получить видео с аудио: {video_url} — пропуск")
    return None
