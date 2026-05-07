"""
Модуль загрузки видео на YouTube.
- OAuth 2.0 авторизация
- Загрузка с отложкой (scheduling)
- Категория: Фильмы и анимация (id=1)
- madeForKids: false
"""

import json
import logging
import os
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from typing import Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_FILE = "youtube_token.json"

# Категории YouTube:
# 1  = Film & Animation
# 2  = Autos & Vehicles
# 10 = Music
# ... и т.д.
CATEGORY_FILM_ANIMATION = "1"


def get_youtube_service(client_secret_file: str):
    """
    Получает авторизованный сервис YouTube API.
    При первом запуске откроет браузер для OAuth.
    """
    creds = None

    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception as e:
            logger.warning(f"Ошибка чтения токена: {e}")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Обновляем токен YouTube...")
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.error(f"Ошибка обновления токена: {e}")
                creds = None

        if not creds:
            if not os.path.exists(client_secret_file):
                raise FileNotFoundError(
                    f"Файл {client_secret_file} не найден!\n"
                    "Скачайте его из Google Cloud Console → APIs → Credentials"
                )
            logger.info("Запускаем OAuth авторизацию YouTube...")
            flow = InstalledAppFlow.from_client_secrets_file(
                client_secret_file, SCOPES
            )

            # access_type=offline + prompt=consent гарантируют выдачу refresh_token,
            # чтобы потом обновлять access без повторной авторизации.
            try:
                creds = flow.run_local_server(
                    port=0,
                    open_browser=True,
                    access_type="offline",
                    prompt="consent",
                )
            except webbrowser.Error:
                logger.info(
                    "Браузер недоступен — используем консольный режим. "
                    "Откройте ссылку на машине с браузером и вставьте код сюда."
                )
                creds = _run_console_flow(flow)

        # Сохраняем токен
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        logger.info("Токен YouTube сохранён")

    return build("youtube", "v3", credentials=creds)


def _run_console_flow(flow: InstalledAppFlow) -> Credentials:
    """OAuth без браузера: пользователь сам открывает ссылку и вставляет код."""
    flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )
    print("\n=== YouTube OAuth ===")
    print("1. Откройте ссылку в браузере (на любой машине):")
    print(auth_url)
    print("2. Авторизуйтесь и скопируйте код.")
    code = input("Вставьте код сюда: ").strip()
    flow.fetch_token(code=code)
    return flow.credentials


def upload_video(
    youtube_service,
    video_path: str,
    title: str,
    description: str = "",
    category_id: str = CATEGORY_FILM_ANIMATION,
    publish_at: Optional[datetime] = None,
    tags: Optional[list[str]] = None,
) -> Optional[str]:
    """
    Загружает видео на YouTube.

    Args:
        youtube_service: авторизованный сервис
        video_path: путь к видео файлу
        title: название видео
        description: описание
        category_id: ID категории (1 = Фильмы и анимация)
        publish_at: время публикации (None = сразу, datetime = отложка)
        tags: теги видео

    Returns:
        video_id или None при ошибке
    """
    if not os.path.exists(video_path):
        logger.error(f"Файл не найден: {video_path}")
        return None

    # Формируем статус
    if publish_at:
        # Отложенная публикация: ставим private + publishAt
        publish_str = publish_at.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        status = {
            "privacyStatus": "private",
            "publishAt": publish_str,
            "selfDeclaredMadeForKids": False,
        }
        logger.info(f"Отложенная публикация: {publish_str}")
    else:
        # Публикация сразу
        status = {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        }

    body = {
        "snippet": {
            "title": title[:100],  # YouTube лимит
            "description": description[:5000],
            "tags": tags or [],
            "categoryId": category_id,
            "defaultLanguage": "ru",
        },
        "status": status,
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=10 * 1024 * 1024,  # 10MB чанки
    )

    logger.info(f"Загружаем на YouTube: '{title}'")

    try:
        request = youtube_service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = _resumable_upload(request)
        if response:
            video_id = response["id"]
            logger.info(
                f"Видео загружено! ID: {video_id} "
                f"URL: https://youtube.com/shorts/{video_id}"
            )
            return video_id

    except HttpError as e:
        logger.error(f"YouTube API ошибка: {e}")
    except Exception as e:
        logger.error(f"Ошибка загрузки: {e}")

    return None


def _resumable_upload(request, max_retries: int = 5) -> Optional[dict]:
    """Загрузка с повторами при ошибках."""
    response = None
    retry = 0

    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                logger.info(f"Загрузка: {progress}%")

        except HttpError as e:
            if e.resp.status in [500, 502, 503, 504]:
                retry += 1
                if retry > max_retries:
                    logger.error("Превышено число повторов")
                    return None
                wait = 2 ** retry
                logger.warning(f"Ошибка сервера, повтор через {wait}с...")
                time.sleep(wait)
            else:
                raise

    return response


def calculate_schedule_times(
    count: int,
    interval_hours: int = 2,
    start_from_now: bool = True,
) -> list[Optional[datetime]]:
    """
    Рассчитывает расписание публикации.
    Первое видео — сразу (None), остальные — через interval_hours.

    Returns:
        Список datetime (или None для немедленной публикации)
    """
    schedule = []
    now = datetime.now(timezone.utc)

    for i in range(count):
        if i == 0 and start_from_now:
            schedule.append(None)  # Сразу
        else:
            publish_time = now + timedelta(hours=interval_hours * i)
            schedule.append(publish_time)

    return schedule
