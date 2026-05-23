"""
Модуль управления состоянием.
Хранит обработанные видео, флаг первого запуска, очередь отложки.
"""

import json
import os
import threading
from datetime import datetime

STATE_FILE = "state.json"

_lock = threading.Lock()


def _default_state() -> dict:
    return {
        "first_run_done": False,
        "processed_videos": [],
        "scheduled_queue": [],
        "last_check": {},
        "channel_ids": {},
    }


def load_state() -> dict:
    with _lock:
        if not os.path.exists(STATE_FILE):
            return _default_state()
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return _default_state()


def save_state(state: dict) -> None:
    with _lock:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)


def is_processed(state: dict, video_id: str) -> bool:
    return video_id in state.get("processed_videos", [])


def mark_processed(state: dict, video_id: str) -> None:
    if video_id not in state["processed_videos"]:
        state["processed_videos"].append(video_id)
    save_state(state)


def is_first_run(state: dict) -> bool:
    return not state.get("first_run_done", False)


def mark_first_run_done(state: dict) -> None:
    state["first_run_done"] = True
    save_state(state)


def get_channel_id(state: dict, username: str) -> str:
    return state.get("channel_ids", {}).get(username, "") or ""


def set_channel_id(state: dict, username: str, channel_id: str) -> None:
    if not channel_id:
        return
    ids = state.setdefault("channel_ids", {})
    if ids.get(username) == channel_id:
        return
    ids[username] = channel_id
    save_state(state)
