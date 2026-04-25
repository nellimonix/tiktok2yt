"""
Модуль обработки видео.
- Наложение полупрозрачного текста "Лимонный чай"
- Обрезка видео до 2:59 если > 3 минут
"""

import logging
import os
import subprocess
import json
from typing import Optional

logger = logging.getLogger(__name__)


def get_video_duration(input_path: str) -> float:
    """Получает длительность видео в секундах."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        input_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except Exception as e:
        logger.error(f"Не удалось получить длительность: {e}")
        return 0


def find_font() -> str:
    """Ищет шрифт с поддержкой кириллицы (Linux + Windows)."""
    candidates = [
        # Локальный шрифт проекта (приоритет)
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "font", "14076.ttf"),
        # Windows
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    ]
    for font in candidates:
        if os.path.exists(font):
            return font
    # Linux: поиск через fc-list
    try:
        result = subprocess.run(
            ["fc-list", ":lang=ru", "-f", "%{file}\n"],
            capture_output=True, text=True, timeout=10
        )
        fonts = result.stdout.strip().split("\n")
        if fonts and fonts[0]:
            return fonts[0]
    except Exception:
        pass
    return "DejaVuSans"


def process_video(
    input_path: str,
    output_path: str,
    overlay_text: str = "Лимонный чай",
    font_size: int = 52,
    opacity: float = 0.5,
    max_duration: int = 179,
) -> Optional[str]:
    """
    Обрабатывает видео:
    1. Накладывает полупрозрачный текст ниже центра
    2. Обрезает до max_duration секунд если видео длиннее

    Возвращает путь к обработанному файлу или None.
    """
    duration = get_video_duration(input_path)
    logger.info(f"Длительность видео: {duration:.1f}с")

    font_path = find_font()
    logger.info(f"Используемый шрифт: {font_path}")

    # FFmpeg drawtext требует экранирования ':' и '\' в путях
    font_escaped = font_path.replace("\\", "/").replace(":", "\\:")

    # Формируем drawtext фильтр
    # Позиция: по центру горизонтально, 60% от верха (чуть ниже середины)
    alpha = opacity
    drawtext = (
        f"drawtext="
        f"text='{overlay_text}':"
        f"fontfile='{font_escaped}':"
        f"fontsize={font_size}:"
        f"fontcolor=white@{alpha}:"
        f"borderw=2:"
        f"bordercolor=black@{alpha * 0.6:.1f}:"
        f"x=(w-text_w)/2:"
        f"y=(h*0.6)"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_path,
    ]

    # Обрезка если нужна
    if duration > max_duration:
        cmd.extend(["-t", str(max_duration)])
        logger.info(f"Видео будет обрезано до {max_duration}с")

    cmd.extend([
        "-vf", drawtext,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ])

    logger.info(f"Обрабатываем видео: {input_path} -> {output_path}")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600
        )
        if result.returncode != 0:
            logger.error(f"FFmpeg ошибка: {result.stderr[-500:]}")
            return None
    except subprocess.TimeoutExpired:
        logger.error("Таймаут FFmpeg")
        return None

    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        logger.info(f"Видео обработано: {output_path} ({size_mb:.1f} МБ)")
        return output_path

    logger.error(f"Выходной файл не найден: {output_path}")
    return None
