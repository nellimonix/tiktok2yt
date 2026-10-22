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
  "api_key": "nvapi-...",                   // Ключ LLM API
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

Выходные видео имеют размер 1080×1920. Водяной знак рисуется через Pillow
в прозрачный PNG и накладывается после масштабирования видео. Ширина надписи
с подложкой ограничена 80% кадра; при необходимости размер шрифта уменьшается.
Кириллица не проходит через `drawtext` FFmpeg.

Для исходников 9:16 размытие фона пропускается. Для остальных оно считается
на уменьшенном фоне 270×480. FFmpeg использует один поток декодирования,
один поток фильтров и один поток кодирования с пресетом `veryfast`.

```bash
python3 main.py
```

Просмотр и повторная обработка ошибок:

```bash
./venv/bin/python main.py --list-failed
./venv/bin/python main.py --retry-failed
```

`processed_videos` содержит только TikTok ID видео, для которых YouTube вернул
корректный ID. Текущие этапы хранятся в `video_states`, ошибки — в
`failed_videos`. Запись `state.json` выполняется атомарно.

При первом запуске откроется браузер для авторизации YouTube.

## Ошибка TikTok `Unexpected response from webpage request`

В зависимостях закреплены `yt-dlp==2026.8.19` с исправлением TikTok extractor
и `curl-cffi==0.16.0`. Более новый `curl-cffi` может выбирать профиль Chrome 150,
для которого [зарегистрирована блокировка TikTok](https://github.com/yt-dlp/yt-dlp/issues/17604).
Исправление extractor описано в [yt-dlp #17403](https://github.com/yt-dlp/yt-dlp/issues/17403).

Обновите зависимости тем Python, который указан в `ExecStart` сервиса.
Для установки в `venv`:

```bash
./venv/bin/python -m pip install --upgrade -r requirements.txt
./venv/bin/python -m yt_dlp --version
./venv/bin/python -m yt_dlp --list-impersonate-targets
```

Проверить извлечение видео без загрузки на YouTube:

```bash
./venv/bin/python -m yt_dlp --ignore-config --verbose --simulate --impersonate chrome 'TIKTOK_VIDEO_URL'
```

После успешной проверки перезапустите сервис. Для повторной обработки
сохранённых ошибок остановите сервис, выполните
`./venv/bin/python main.py --retry-failed`, затем запустите сервис снова.
Команда повторной обработки загружает готовые видео на YouTube.

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

На сервере с другими сервисами установите ограничение CPU:

```bash
sudo install -D -m 644 deploy/tiktok2yt-resources.conf /etc/systemd/system/tiktok2yt.service.d/resources.conf
sudo systemctl daemon-reload
sudo systemctl restart tiktok2yt
```

Лимит 70% относится к одному ядру и охватывает весь сервис вместе с дочерними
процессами, включая FFmpeg и Whisper. На сервере с двумя ядрами это около 35%
общей мощности CPU. Обработка ограничена доступным CPU-временем, поэтому
длинные ролики могут занимать больше времени.

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
