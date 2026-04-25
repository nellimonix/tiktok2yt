"""
Модуль транскрипции через faster-whisper.
Извлекает текст из аудиодорожки видео.
"""

import logging
import os
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

# Глобальная модель (загружается один раз)
_model = None


def _get_model(model_size: str = "base"):
    """Ленивая загрузка модели Whisper."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        logger.info(f"Загружаем модель Whisper: {model_size}")
        _model = WhisperModel(
            model_size,
            device="cpu",
            compute_type="int8",
            cpu_threads=4,
        )
        logger.info("Модель Whisper загружена")
    return _model


def extract_audio(video_path: str, audio_path: str) -> Optional[str]:
    """Извлекает аудио из видео в WAV 16kHz mono."""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        audio_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error(f"Ошибка извлечения аудио: {result.stderr[-300:]}")
            return None
    except subprocess.TimeoutExpired:
        logger.error("Таймаут извлечения аудио")
        return None

    if os.path.exists(audio_path):
        return audio_path
    return None


def transcribe(video_path: str, model_size: str = "base") -> str:
    """
    Транскрибирует видео.
    Возвращает текст или пустую строку если не удалось.
    """
    audio_path = video_path + ".wav"
    try:
        extracted = extract_audio(video_path, audio_path)
        if not extracted:
            logger.warning("Не удалось извлечь аудио, пробуем напрямую")
            audio_path = video_path  # Попробуем подать видео напрямую

        model = _get_model(model_size)
        segments, info = model.transcribe(
            audio_path,
            beam_size=5,
            language=None,  # Авто-определение языка
            vad_filter=True,
        )

        text_parts = []
        for segment in segments:
            text_parts.append(segment.text.strip())

        full_text = " ".join(text_parts)
        logger.info(
            f"Транскрипция: язык={info.language} "
            f"вероятность={info.language_probability:.2f} "
            f"длина={len(full_text)} символов"
        )

        return full_text

    except Exception as e:
        logger.error(f"Ошибка транскрипции: {e}")
        return ""

    finally:
        # Удаляем временный аудио файл
        temp_audio = video_path + ".wav"
        if os.path.exists(temp_audio):
            os.remove(temp_audio)
