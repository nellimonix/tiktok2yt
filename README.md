# TikTok → YouTube Shorts Переливер

Автоматический скрипт, который мониторит TikTok-аккаунты, скачивает новые видео, обрабатывает их и загружает как YouTube Shorts.

## Что делает

1. **Мониторит** указанные TikTok-аккаунты на появление новых видео
2. **Скачивает** видео через `yt-dlp`
3. **Обрабатывает**: накладывает полупрозрачный текст "Лимонный чай", обрезает до 2:59 если длиннее 3 минут
4. **Транскрибирует** аудио через `faster-whisper` (локально)
5. **Генерирует название** через NVIDIA API (google/gemma-3-27b-it)
6. **Загружает** на YouTube: категория "Фильмы и анимация", не для детей

## Первый запуск

При первом запуске скрипт:
- Забирает по 5 видео с каждого аккаунта
- Первое публикует сразу
- Остальные ставит в отложку с интервалом 2 часа
- Затем переходит в режим мониторинга

## Установка

```bash
# 1. Клонируем / копируем проект
cd tiktok2yt

# 2. Устанавливаем зависимости
chmod +x setup.sh
./setup.sh

# 3. Настраиваем конфиг
cp config.example.json config.json
nano config.json
```

## Настройка Google Cloud (YouTube API)

1. Откройте [Google Cloud Console](https://console.cloud.google.com/)
2. Создайте новый проект
3. Включите **YouTube Data API v3**
4. Перейдите в **Credentials** → **Create Credentials** → **OAuth 2.0 Client ID**
5. Тип: **Desktop application**
6. Скачайте JSON и сохраните как `client_secret.json` в папку проекта
7. В **OAuth consent screen** добавьте свой email в тестовые пользователи

## Настройка config.json

```json
{
  "tiktok_accounts": ["user1", "user2"],    // TikTok юзернеймы
  "nvidia_api_key": "nvapi-...",            // Ключ NVIDIA API
  "youtube_client_secret": "client_secret.json",
  "whisper_model": "base",                  // base / small / medium
  "check_interval_minutes": 5,              // Интервал проверки
  "schedule_interval_hours": 2,             // Интервал отложки
  "initial_videos_per_account": 5,          // Видео на первом запуске
  "overlay_text": "Лимонный чай",
  "overlay_font_size": 52,
  "overlay_opacity": 0.5,
  "max_duration_seconds": 179,
  "youtube_category_id": "1",
  "temp_dir": "tmp",
  "log_file": "bot.log"
}
```

## Запуск

```bash
python3 main.py
```

При первом запуске откроется браузер для авторизации YouTube.

## Запуск в фоне (systemd)

```bash
sudo nano /etc/systemd/system/tiktok2yt.service
```

```ini
[Unit]
Description=TikTok to YouTube Shorts
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/tiktok2yt
ExecStart=/usr/bin/python3 main.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable tiktok2yt
sudo systemctl start tiktok2yt
sudo journalctl -u tiktok2yt -f   # логи
```

## Структура файлов

```
tiktok2yt/
├── main.py              # Оркестратор
├── monitor.py           # Мониторинг TikTok
├── video_processor.py   # FFmpeg обработка
├── transcriber.py       # Whisper транскрипция
├── ai_title.py          # NVIDIA API генерация названий
├── uploader.py          # YouTube загрузка
├── state_manager.py     # Хранение состояния
├── config.json          # Настройки (создать из example)
├── config.example.json  # Шаблон настроек
├── requirements.txt     # Python зависимости
├── setup.sh             # Скрипт установки
├── state.json           # Автоматически: состояние бота
├── youtube_token.json   # Автоматически: OAuth токен
└── bot.log              # Автоматически: логи
```

## Модели Whisper и ресурсы

| Модель | RAM    | Точность | Скорость |
|--------|--------|----------|----------|
| base   | ~1 ГБ  | Хорошая  | Быстрая  |
| small  | ~2 ГБ  | Лучше    | Средняя  |
| medium | ~5 ГБ  | Отличная | Медленная|

С 24 ГБ RAM рекомендуется `base` или `small`.
