#!/usr/bin/env python3
"""
TikTok → YouTube Shorts переливер.

Главный оркестратор:
- Первый запуск: забирает N видео с каждого аккаунта, ставит в отложку
- Далее: мониторит аккаунты, обрабатывает новые видео
"""

import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime, timezone

from state_manager import (
    load_state, save_state, is_processed,
    mark_processed, is_first_run, mark_first_run_done,
    get_channel_id, set_channel_id,
)
from monitor import get_video_list, download_video
from video_processor import process_video
from transcriber import transcribe
from ai_title import generate_title
from uploader import (
    get_youtube_service, upload_video, calculate_schedule_times,
)

# ─────────────────────────────────────────────
# Настройки
# ─────────────────────────────────────────────

CONFIG_FILE = "config.json"


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        print(
            f"ОШИБКА: {CONFIG_FILE} не найден!\n"
            f"Скопируйте config.example.json → config.json и заполните."
        )
        sys.exit(1)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def setup_logging(log_file: str) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Пайплайн обработки одного видео
# ─────────────────────────────────────────────

def process_single_video(
    video_info: dict,
    config: dict,
    youtube_service,
    publish_at=None,
) -> bool:
    """
    Полный пайплайн для одного видео:
    скачать → обработать → транскрибировать → название → загрузить

    Returns:
        True если всё успешно
    """
    vid_id = video_info["id"]
    tmp_dir = config.get("temp_dir", "tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    raw_path = os.path.join(tmp_dir, f"{vid_id}_raw.mp4")
    processed_path = os.path.join(tmp_dir, f"{vid_id}_final.mp4")

    try:
        # 1. Скачивание
        logger.info(f"{'='*50}")
        logger.info(f"Обработка видео: {vid_id} (@{video_info.get('username', '?')})")

        downloaded = download_video(video_info["url"], raw_path)
        if not downloaded:
            logger.error(f"Не удалось скачать: {vid_id}")
            return False

        # 2. Обработка (текст + обрезка)
        processed = process_video(
            input_path=downloaded,
            output_path=processed_path,
            overlay_text=config.get("overlay_text", "Лимонный чай"),
            font_size=config.get("overlay_font_size", 52),
            opacity=config.get("overlay_opacity", 0.5),
            max_duration=config.get("max_duration_seconds", 179),
        )
        if not processed:
            logger.error(f"Не удалось обработать: {vid_id}")
            return False

        # 3. Транскрипция
        transcript = transcribe(
            processed,
            model_size=config.get("whisper_model", "base"),
        )
        logger.info(f"Транскрипция ({len(transcript)} символов): {transcript[:100]}...")

        # 4. Генерация названия
        title = generate_title(
            transcript=transcript,
            api_key=config["api_key"],
            api_url=config.get("api_url"),
            model=config.get("model"),
            fallback_title=video_info.get("title", "Интересное видео")[:60],
        )
        logger.info(f"Название: {title}")

        # 5. Загрузка на YouTube
        description = f"#shorts"
        video_id = upload_video(
            youtube_service=youtube_service,
            video_path=processed,
            title=title,
            description=description,
            category_id=config.get("youtube_category_id", "1"),
            publish_at=publish_at,
            tags=["shorts"],
        )

        if video_id:
            schedule_info = (
                f"(отложка: {publish_at.strftime('%Y-%m-%d %H:%M UTC')})"
                if publish_at else "(сразу)"
            )
            logger.info(f"УСПЕХ! YouTube ID: {video_id} {schedule_info}")
            return True
        else:
            logger.error(f"Не удалось загрузить на YouTube: {vid_id}")
            return False

    finally:
        # Очистка временных файлов
        for f in [raw_path, processed_path]:
            if os.path.exists(f):
                os.remove(f)
        # Также убираем файлы которые yt-dlp мог создать с другим расширением
        for ext in [".mp4", ".webm", ".mkv", ".part"]:
            candidate = os.path.join(tmp_dir, f"{vid_id}_raw{ext}")
            if os.path.exists(candidate):
                os.remove(candidate)


# ─────────────────────────────────────────────
# Первый запуск: набираем контент
# ─────────────────────────────────────────────

def first_run(config: dict, state: dict, youtube_service) -> None:
    """
    Первый запуск:
    - Забираем initial_videos_per_account видео с каждого аккаунта
    - Первое публикуем сразу, остальные ставим в отложку через каждые 2 часа
    """
    logger.info("=" * 60)
    logger.info("ПЕРВЫЙ ЗАПУСК — собираем начальный контент")
    logger.info("=" * 60)

    accounts = config["tiktok_accounts"]
    per_account = config.get("initial_videos_per_account", 5)
    interval_hours = config.get("schedule_interval_hours", 2)

    all_videos = []

    for username in accounts:
        videos = get_video_list(
            username,
            max_count=per_account,
            channel_id=get_channel_id(state, username),
        )
        for v in videos:
            cid = v.get("channel_id")
            if cid:
                set_channel_id(state, username, cid)
                break
        for v in videos:
            if not is_processed(state, v["id"]):
                all_videos.append(v)

    if not all_videos:
        logger.info("Нет новых видео для первого запуска")
        mark_first_run_done(state)
        return

    logger.info(f"Найдено {len(all_videos)} видео для первого запуска")

    # Рассчитываем расписание
    schedule = calculate_schedule_times(
        count=len(all_videos),
        interval_hours=interval_hours,
        start_from_now=True,
    )

    success_count = 0
    for i, (video, pub_time) in enumerate(zip(all_videos, schedule)):
        logger.info(f"\n--- Видео {i+1}/{len(all_videos)} ---")

        ok = process_single_video(
            video_info=video,
            config=config,
            youtube_service=youtube_service,
            publish_at=pub_time,
        )
        if ok:
            mark_processed(state, video["id"])
            success_count += 1
        else:
            logger.warning(f"Пропускаем видео {video['id']}")
            # Всё равно помечаем чтобы не пытаться снова
            mark_processed(state, video["id"])

        # Пауза между загрузками чтобы не нарваться на rate limit
        if i < len(all_videos) - 1:
            time.sleep(5)

    logger.info(f"\nПервый запуск завершён: {success_count}/{len(all_videos)} загружено")
    mark_first_run_done(state)


# ─────────────────────────────────────────────
# Основной цикл мониторинга
# ─────────────────────────────────────────────

def monitoring_loop(config: dict, state: dict, youtube_service) -> None:
    """
    Бесконечный цикл: проверяем аккаунты, обрабатываем новые видео.
    """
    interval = config.get("check_interval_minutes", 5) * 60
    accounts = config["tiktok_accounts"]

    logger.info("=" * 60)
    logger.info(f"Запущен мониторинг ({len(accounts)} аккаунтов)")
    logger.info(f"Интервал проверки: {interval // 60} мин")
    logger.info("=" * 60)

    while True:
        try:
            for username in accounts:
                logger.info(f"\nПроверяем @{username}...")

                # Берём последние 5 видео
                videos = get_video_list(
                    username,
                    max_count=5,
                    channel_id=get_channel_id(state, username),
                )

                for v in videos:
                    cid = v.get("channel_id")
                    if cid:
                        set_channel_id(state, username, cid)
                        break

                new_count = 0
                for video in videos:
                    if is_processed(state, video["id"]):
                        continue

                    logger.info(f"Новое видео: {video['id']} от @{username}")
                    new_count += 1

                    ok = process_single_video(
                        video_info=video,
                        config=config,
                        youtube_service=youtube_service,
                        publish_at=None,  # Новые видео публикуем сразу
                    )
                    mark_processed(state, video["id"])

                    if ok:
                        logger.info(f"Видео {video['id']} успешно обработано")
                    else:
                        logger.warning(f"Не удалось обработать {video['id']}")

                    time.sleep(3)

                if new_count == 0:
                    logger.info(f"@{username}: нет новых видео")

        except KeyboardInterrupt:
            logger.info("\nОстановка по Ctrl+C")
            break
        except Exception as e:
            logger.error(f"Ошибка в цикле мониторинга: {e}", exc_info=True)

        logger.info(f"\nСледующая проверка через {interval // 60} мин...")
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("\nОстановка по Ctrl+C")
            break


# ─────────────────────────────────────────────
# Точка входа
# ─────────────────────────────────────────────

def main():
    config = load_config()
    setup_logging(config.get("log_file", "bot.log"))

    logger.info("TikTok → YouTube Shorts переливер запущен")

    # Создаём временную директорию
    tmp_dir = config.get("temp_dir", "tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    # Авторизация YouTube (при первом запуске откроет браузер)
    logger.info("Авторизация YouTube...")
    youtube_service = get_youtube_service(config["youtube_client_secret"])
    logger.info("YouTube авторизован!")

    # Загружаем состояние
    state = load_state()

    # Первый запуск?
    if is_first_run(state):
        first_run(config, state, youtube_service)
    else:
        logger.info("Первый запуск уже выполнен, переходим к мониторингу")

    # Основной цикл
    monitoring_loop(config, state, youtube_service)


if __name__ == "__main__":
    main()
