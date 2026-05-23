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


def download_video(video_url: str, output_path: str) -> Optional[str]:
    """
    Скачивает видео без водяного знака TikTok.
    Возвращает путь к скачанному файлу или None.
    """
    cmd = [
        "yt-dlp",
        "-f", "best",
        "-o", output_path,
        "--no-warnings",
        "--merge-output-format", "mp4",
        video_url,
    ]
    logger.info(f"Скачиваем: {video_url}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            logger.error(f"Ошибка скачивания: {result.stderr}")
            return None
    except subprocess.TimeoutExpired:
        logger.error(f"Таймаут при скачивании: {video_url}")
        return None

    # yt-dlp может добавить расширение
    for ext in ["", ".mp4", ".webm"]:
        candidate = output_path + ext if not output_path.endswith(ext) else output_path
        if os.path.exists(candidate):
            logger.info(f"Скачано: {candidate}")
            return candidate

    # Проверяем без расширения (yt-dlp мог подставить)
    base = os.path.splitext(output_path)[0]
    for ext in [".mp4", ".webm", ".mkv"]:
        candidate = base + ext
        if os.path.exists(candidate):
            return candidate

    logger.error(f"Файл не найден после скачивания: {output_path}")
    return None
