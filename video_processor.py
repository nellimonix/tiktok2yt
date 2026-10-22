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
import tempfile
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

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


def render_watermark(
    output_path: str,
    overlay_text: str,
    font_path: str,
    font_size: int,
    opacity: float,
    output_width: int = SHORTS_WIDTH,
    output_height: int = SHORTS_HEIGHT,
) -> None:
    padding = 12
    max_width = max(1, round(output_width * 0.8) - padding * 2)
    max_height = max(1, round(output_height * 0.15) - padding * 2)
    rendered_font_size = max(1, font_size, round(output_width * 0.102))
    opacity = min(1.0, max(0.0, opacity))
    while True:
        font = ImageFont.truetype(font_path, rendered_font_size)
        bounds = font.getbbox(overlay_text, stroke_width=2)
        width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        if (width <= max_width and height <= max_height) or rendered_font_size == 1:
            break
        ratio = min(max_width / max(1, width), max_height / max(1, height))
        rendered_font_size = max(1, min(rendered_font_size - 1, int(rendered_font_size * ratio)))
    image_size = (max(1, width) + padding * 2, max(1, height) + padding * 2)
    background = Image.new("RGBA", image_size, (0, 0, 0, round(255 * min(0.35, opacity * 0.35))))
    foreground = Image.new("RGBA", image_size)
    ImageDraw.Draw(foreground).text(
        (padding - bounds[0], padding - bounds[1]),
        overlay_text,
        font=font,
        fill=(255, 255, 255, round(255 * opacity)),
        stroke_width=2,
        stroke_fill=(0, 0, 0, round(255 * opacity * 0.8)),
    )
    Image.alpha_composite(background, foreground).save(output_path)


def build_shorts_filter(
    output_width: int = SHORTS_WIDTH,
    output_height: int = SHORTS_HEIGHT,
    source_width: int = 0,
    source_height: int = 0,
) -> str:
    if source_width > 0 and source_height > 0 and source_width * output_height == source_height * output_width:
        composition = f"[0:v]scale={output_width}:{output_height},setsar=1[composed];"
    else:
        background_width = min(output_width, 270)
        background_height = max(2, round(background_width * output_height / output_width / 2) * 2)
        composition = (
            f"[0:v]split=2[background][foreground];"
            f"[background]"
            f"scale={background_width}:{background_height}:force_original_aspect_ratio=increase,"
            f"crop={background_width}:{background_height},boxblur=5:1,"
            f"scale={output_width}:{output_height},setsar=1[background_blurred];"
            f"[foreground]"
            f"scale={output_width}:{output_height}:force_original_aspect_ratio=decrease,"
            f"setsar=1[foreground_scaled];"
            f"[background_blurred][foreground_scaled]"
            f"overlay=(W-w)/2:(H-h)/2:shortest=1[composed];"
        )
    return composition + (
        f"[composed][1:v]overlay=x=(W-w)/2:y=(H-h)*0.75,"
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
        output_width=output_width,
        output_height=output_height,
        source_width=source_width,
        source_height=source_height,
    )

    output_args = []

    # Обрезка если нужна
    if duration > max_duration:
        output_args.extend(["-t", str(max_duration)])
        logger.info(f"Видео будет обрезано до {max_duration}с")

    output_args.extend([
        "-filter_complex", video_filter,
        "-map", "[video]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-threads:v", "1",
        "-threads:a", "1",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ])

    logger.info(f"Обрабатываем видео: {input_path} -> {output_path}")

    try:
        with tempfile.TemporaryDirectory(prefix="tiktok-watermark-") as watermark_dir:
            watermark_path = os.path.join(watermark_dir, "overlay.png")
            render_watermark(
                watermark_path, overlay_text, font_path, font_size, opacity,
                output_width, output_height,
            )
            cmd = [
                "ffmpeg", "-y", "-nostdin",
                "-filter_complex_threads", "1",
                "-threads", "1", "-i", input_path,
                "-threads", "1", "-i", watermark_path,
            ] + output_args
            logger.info("Водяной знак: %r", overlay_text)
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600
            )
        if result.returncode != 0:
            logger.error(f"FFmpeg ошибка: {result.stderr[-500:]}")
            return None
    except subprocess.TimeoutExpired:
        logger.error("Таймаут FFmpeg")
        return None
    except (OSError, ValueError) as error:
        logger.error("Ошибка обработки видео: %s", error)
        return None

    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        logger.info(f"Видео обработано: {output_path} ({size_mb:.1f} МБ)")
        return output_path

    logger.error(f"Выходной файл не найден: {output_path}")
    return None
