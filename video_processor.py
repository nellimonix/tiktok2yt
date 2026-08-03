"""
Модуль обработки видео.
- Наложение полупрозрачного текста "Лимонный чай"
- Обрезка видео до 2:59 если > 3 минут
- Приведение к вертикальному формату YouTube Shorts 1080x1920
"""

import logging
import os
import subprocess
import json
from typing import Optional

logger = logging.getLogger(__name__)

SHORTS_WIDTH = 1080
SHORTS_HEIGHT = 1920


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


def get_video_dimensions(input_path: str) -> tuple[int, int]:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        input_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except Exception as e:
        logger.error(f"Не удалось получить размер видео: {e}")
        return 0, 0


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


def _escape_drawtext(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
    )


def build_shorts_filter(
    overlay_text: str,
    font_path: str,
    font_size: int,
    opacity: float,
    output_width: int = SHORTS_WIDTH,
    output_height: int = SHORTS_HEIGHT,
) -> str:
    font_escaped = font_path.replace("\\", "/").replace(":", "\\:")
    text_escaped = _escape_drawtext(overlay_text)
    rendered_font_size = max(font_size, round(output_width * 0.102))
    border_opacity = min(1.0, opacity * 0.8)
    box_opacity = min(0.35, opacity * 0.35)
    return (
        f"[0:v]split=2[background][foreground];"
        f"[background]"
        f"scale={output_width}:{output_height}:force_original_aspect_ratio=increase,"
        f"crop={output_width}:{output_height},boxblur=20:1[background_blurred];"
        f"[foreground]"
        f"scale={output_width}:{output_height}:force_original_aspect_ratio=decrease,"
        f"setsar=1[foreground_scaled];"
        f"[background_blurred][foreground_scaled]"
        f"overlay=(W-w)/2:(H-h)/2:shortest=1,"
        f"drawtext="
        f"text='{text_escaped}':"
        f"fontfile='{font_escaped}':"
        f"fontsize={rendered_font_size}:"
        f"fontcolor=white@{opacity}:"
        f"borderw=2:"
        f"bordercolor=black@{border_opacity:.2f}:"
        f"box=1:"
        f"boxcolor=black@{box_opacity:.2f}:"
        f"boxborderw=12:"
        f"x=(w-text_w)/2:"
        f"y=(h-text_h)*0.75,"
        f"format=yuv420p,setsar=1[video]"
    )


def process_video(
    input_path: str,
    output_path: str,
    overlay_text: str = "Лимонный чай",
    font_size: int = 52,
    opacity: float = 0.5,
    max_duration: int = 179,
    output_width: int = SHORTS_WIDTH,
    output_height: int = SHORTS_HEIGHT,
) -> Optional[str]:
    """
    Обрабатывает видео:
    1. Приводит видео к вертикальному формату 9:16
    2. Накладывает полупрозрачный текст
    3. Обрезает до max_duration секунд если видео длиннее

    Возвращает путь к обработанному файлу или None.
    """
    duration = get_video_duration(input_path)
    logger.info(f"Длительность видео: {duration:.1f}с")

    source_width, source_height = get_video_dimensions(input_path)
    logger.info(
        f"Формат видео: {source_width}x{source_height} -> "
        f"{output_width}x{output_height} (9:16)"
    )

    font_path = find_font()
    logger.info(f"Используемый шрифт: {font_path}")

    video_filter = build_shorts_filter(
        overlay_text=overlay_text,
        font_path=font_path,
        font_size=font_size,
        opacity=opacity,
        output_width=output_width,
        output_height=output_height,
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
        "-filter_complex", video_filter,
        "-map", "[video]",
        "-map", "0:a?",
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
