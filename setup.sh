#!/bin/bash
# Установка зависимостей для TikTok → YouTube Shorts переливера

set -e

echo "=== TikTok → YouTube Shorts: Установка ==="

# Системные зависимости
echo "[1/3] Устанавливаем системные пакеты..."
sudo apt-get update -qq
sudo apt-get install -y -qq ffmpeg

# Python зависимости
echo "[2/3] Устанавливаем Python пакеты..."
pip install -r requirements.txt

# Проверяем всё
echo "[3/3] Проверяем установку..."

echo -n "  ffmpeg: "
ffmpeg -version 2>&1 | head -1

echo -n "  yt-dlp: "
yt-dlp --version

python3 -c "from faster_whisper import WhisperModel; print('  faster-whisper: OK')"
python3 -c "from googleapiclient.discovery import build; print('  google-api: OK')"

echo ""
echo "=== Установка завершена ==="
echo ""
echo "Следующие шаги:"
echo "  1. Скопируйте config.example.json → config.json"
echo "  2. Заполните tiktok_accounts, nvidia_api_key"
echo "  3. Положите client_secret.json (Google Cloud OAuth)"
echo "  4. Запустите: python3 main.py"
echo ""
echo "При первом запуске откроется браузер для авторизации YouTube."
