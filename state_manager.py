"""Управление состоянием обработки видео."""

import json
import os
import re
import shutil
import threading
from datetime import datetime, timezone

STATE_FILE = "state.json"

STATUS_FOUND = "found"
STATUS_PROCESSING = "processing"
STATUS_UPLOADED = "uploaded"
STATUS_ERROR = "error"
YOUTUBE_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_lock = threading.RLock()


def _default_state() -> dict:
    return {
        "first_run_done": False,
        "processed_videos": [],
        "failed_videos": [],
        "video_states": {},
        "scheduled_queue": [],
        "last_check": {},
        "channel_ids": {},
    }


def _ensure_schema(state: dict) -> dict:
    defaults = _default_state()
    if not isinstance(state, dict):
        return defaults
    for key, value in defaults.items():
        if key not in state or not isinstance(state[key], type(value)):
            state[key] = value
    return state


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state() -> dict:
    with _lock:
        if not os.path.exists(STATE_FILE):
            return _default_state()
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as handle:
                state = json.load(handle)
        except (json.JSONDecodeError, OSError) as error:
            raise RuntimeError(f"Cannot read {STATE_FILE}: {error}") from error

        needs_migration = not isinstance(state, dict) or any(
            key not in state for key in ("failed_videos", "video_states")
        )
        state = _ensure_schema(state)
        if needs_migration:
            backup_directory = os.path.join(
                os.path.dirname(os.path.abspath(STATE_FILE)),
                "state_backups",
            )
            os.makedirs(backup_directory, exist_ok=True)
            backup_name = datetime.now(timezone.utc).strftime(
                "state-%Y%m%dT%H%M%SZ.json"
            )
            shutil.copy2(
                STATE_FILE,
                os.path.join(backup_directory, backup_name),
            )
            save_state(state)
        return state


def save_state(state: dict) -> None:
    with _lock:
        state = _ensure_schema(state)
        target = os.path.abspath(STATE_FILE)
        directory = os.path.dirname(target)
        os.makedirs(directory, exist_ok=True)
        temporary = f"{target}.tmp.{os.getpid()}.{threading.get_ident()}"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)


def is_processed(state: dict, video_id: str) -> bool:
    return str(video_id) in state.get("processed_videos", [])


def _video_record(video: dict) -> dict:
    return {
        "tiktok_id": str(video["id"]),
        "url": video.get("url", ""),
        "account": video.get("username", ""),
        "title": video.get("title", ""),
        "duration": video.get("duration", 0),
    }


def mark_found(state: dict, video: dict) -> None:
    video_id = str(video["id"])
    if is_processed(state, video_id):
        return
    record = _video_record(video)
    record.update({"status": STATUS_FOUND, "updated_at": _utc_now()})
    state.setdefault("video_states", {})[video_id] = record
    save_state(state)


def mark_stage(
    state: dict,
    video: dict,
    stage: str,
    local_file_path: str = "",
) -> None:
    video_id = str(video["id"])
    record = _video_record(video)
    record.update({
        "status": STATUS_PROCESSING,
        "stage": stage,
        "local_file_path": local_file_path,
        "updated_at": _utc_now(),
    })
    state.setdefault("video_states", {})[video_id] = record
    save_state(state)


def _find_failure(state: dict, video_id: str) -> dict | None:
    for failure in state.setdefault("failed_videos", []):
        if str(failure.get("tiktok_id")) == str(video_id):
            return failure
    return None


def record_failure(
    state: dict,
    video: dict,
    stage: str,
    error: str,
    local_file_path: str = "",
) -> None:
    now = _utc_now()
    video_id = str(video["id"])
    failure = _find_failure(state, video_id)
    if failure is None:
        failure = {
            **_video_record(video),
            "stage": stage,
            "error": str(error),
            "attempts": 1,
            "first_error_at": now,
            "last_attempt_at": now,
            "local_file_path": local_file_path,
        }
        state["failed_videos"].append(failure)
    else:
        failure.update(_video_record(video))
        failure.update({
            "stage": stage,
            "error": str(error),
            "attempts": int(failure.get("attempts", 0)) + 1,
            "last_attempt_at": now,
        })
        failure["local_file_path"] = local_file_path

    video_state = _video_record(video)
    video_state.update({
        "status": STATUS_ERROR,
        "stage": stage,
        "error": str(error),
        "updated_at": now,
    })
    state.setdefault("video_states", {})[video_id] = video_state
    save_state(state)


def remove_failure(state: dict, video_id: str) -> None:
    video_id = str(video_id)
    failures = state.setdefault("failed_videos", [])
    remaining = [
        failure for failure in failures
        if str(failure.get("tiktok_id")) != video_id
    ]
    if len(remaining) != len(failures):
        state["failed_videos"] = remaining
        save_state(state)


def mark_uploaded(state: dict, video: dict, youtube_id: str) -> None:
    if not YOUTUBE_VIDEO_ID_RE.fullmatch(str(youtube_id or "")):
        raise ValueError("Invalid YouTube video ID")
    video_id = str(video["id"])
    if video_id not in state["processed_videos"]:
        state["processed_videos"].append(video_id)
    state["failed_videos"] = [
        failure for failure in state.setdefault("failed_videos", [])
        if str(failure.get("tiktok_id")) != video_id
    ]
    record = _video_record(video)
    record.update({
        "status": STATUS_UPLOADED,
        "youtube_id": youtube_id,
        "updated_at": _utc_now(),
    })
    state.setdefault("video_states", {})[video_id] = record
    save_state(state)


def recover_interrupted(state: dict) -> int:
    interrupted = [
        record.copy()
        for record in state.get("video_states", {}).values()
        if record.get("status") == STATUS_PROCESSING
    ]
    for record in interrupted:
        video = {
            "id": record["tiktok_id"],
            "url": record.get("url", ""),
            "username": record.get("account", ""),
            "title": record.get("title", ""),
            "duration": record.get("duration", 0),
        }
        record_failure(
            state,
            video,
            record.get("stage", "processing"),
            "Processing was interrupted before completion",
            record.get("local_file_path", ""),
        )
    return len(interrupted)


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
