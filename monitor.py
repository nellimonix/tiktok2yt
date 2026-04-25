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


def get_video_list(username: str, max_count: int = 10) -> list[dict]:
    """
    Получает список последних видео аккаунта.
    Возвращает список словарей: {id, url, title, duration}
    """
    url = f"https://www.tiktok.com/@{username}"
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--playlist-end", str(max_count),
        "--print", "%(id)s|||%(title)s|||%(duration)s|||%(webpage_url)s",
        "--no-warnings",
        url,
    ]
    logger.info(f"Получаем список видео @{username} (макс. {max_count})")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            logger.error(f"yt-dlp ошибка для @{username}: {result.stderr}")
            return []
    except subprocess.TimeoutExpired:
        logger.error(f"Таймаут при получении списка @{username}")
        return []

    videos = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("|||")
        if len(parts) < 4:
            continue
        vid_id, title, duration_str, vid_url = parts
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
        })

    logger.info(f"Найдено {len(videos)} видео у @{username}")
    return videos


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
