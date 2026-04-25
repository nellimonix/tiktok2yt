"""
Модуль генерации названий через NVIDIA API.
Модель: google/gemma-3-27b-it
Ответ строго в JSON, с ретраем при ошибке парсинга.
"""

import json
import logging
import re
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "google/gemma-3-27b-it"
MAX_RETRIES = 3

SYSTEM_PROMPT = """Ты генератор названий для YouTube Shorts.

ЗАДАЧА: придумай одно короткое название для видео.

ЖЁСТКИЕ ОГРАНИЧЕНИЯ:
- Русский язык
- МАКСИМУМ 40 символов (это жёсткий лимит, больше нельзя)
- Идеально: 15–30 символов
- Без хештегов, эмодзи, кавычек, скобок
- Без слов: "shorts", "видео", "ролик", "шок", "топ"
- Без восклицательных знаков
- Одно предложение, без точки в конце

СТИЛЬ: короткий, интригующий, как заголовок в ленте.
Примеры хороших названий: "Когда узнал правду", "Этого никто не ожидал", "Зачем он это сделал"

ОТВЕТ — только JSON, ничего кроме JSON:
{"title": "название"}"""


def generate_title(
    transcript: str,
    api_key: str,
    fallback_title: str = "Интересное видео",
) -> str:
    """
    Генерирует название для YouTube Shorts.
    При неудаче парсинга — повторяет до MAX_RETRIES раз.
    При полной неудаче — возвращает fallback_title.
    """
    if not transcript or len(transcript.strip()) < 5:
        logger.warning("Транскрипция слишком короткая, используем fallback")
        return fallback_title

    # Обрезаем транскрипцию если слишком длинная
    truncated = transcript[:1500]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    for attempt in range(1, MAX_RETRIES + 1):
        logger.info(f"Генерация названия, попытка {attempt}/{MAX_RETRIES}")

        payload = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Транскрипция видео:\n\n{truncated}",
                },
            ],
            "temperature": 0.7,
            "max_tokens": 100,
        }

        try:
            resp = requests.post(
                NVIDIA_API_URL,
                headers=headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            raw = data["choices"][0]["message"]["content"].strip()
            logger.debug(f"Сырой ответ ИИ: {raw}")

            title = _parse_title(raw)
            if title:
                logger.info(f"Сгенерировано название: {title}")
                return title

            logger.warning(f"Не удалось распарсить JSON: {raw}")

        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка API запроса: {e}")
            time.sleep(2)
        except (KeyError, IndexError) as e:
            logger.error(f"Неожиданная структура ответа: {e}")
            time.sleep(1)

    logger.error("Все попытки генерации исчерпаны")
    return fallback_title


def _parse_title(raw_response: str) -> Optional[str]:
    """
    Извлекает title из JSON-ответа.
    Обрабатывает случаи когда модель оборачивает в ```json ... ```
    """
    # Убираем markdown-обёртку если есть
    cleaned = raw_response.strip()
    cleaned = re.sub(r"^```json\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    title = None

    try:
        parsed = json.loads(cleaned)
        title = parsed.get("title", "").strip()
    except json.JSONDecodeError:
        pass

    # Фоллбэк: ищем паттерн "title": "..." регекспом
    if not title:
        match = re.search(r'"title"\s*:\s*"([^"]+)"', raw_response)
        if match:
            title = match.group(1).strip()

    if not title or len(title) < 3:
        return None

    # Очистка
    title = title.strip('"\'«»!.')
    title = re.sub(r'[#\[\]{}]', '', title)

    # Жёсткий лимит — обрезаем до 50 символов по последнему пробелу
    if len(title) > 50:
        title = title[:50].rsplit(' ', 1)[0]

    return title if len(title) >= 3 else None
