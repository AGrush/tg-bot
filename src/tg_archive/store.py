from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from tg_archive.logutil import say
from tg_archive.names import chat_dirname, safe_filename
from tg_archive.policy import MediaPolicy
from tg_archive.render import render_day


@dataclass
class ChatInfo:
    id: int
    title: str
    type: str = "group"
    username: str | None = None
    bot_status: str | None = None


@dataclass
class PrepareResult:
    new: bool
    downloads: list[dict]
    folder: Path


@dataclass
class OpenChat:
    folder: Path
    keys: set[str] = field(default_factory=set)
    media: dict[str, dict] = field(default_factory=dict)
    dirty: set[str] = field(default_factory=set)


class Archive:
    """One directory per chat id. JSONL is the log. The .txt file is for reading."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._open: dict[int, OpenChat] = {}
        self._folders: dict[int, Path] = {}
        self._unsynced: set[Path] = set()

    def chat_folder(self, chat: ChatInfo) -> Path:
        chats = self.root / "chats"
        chats.mkdir(parents=True, exist_ok=True)
        desired = chats / chat_dirname(chat.id, chat.title)
        prefix = f"{chat.id}__"
        matches = [path for path in chats.iterdir() if path.is_dir() and path.name.startswith(prefix)]
        if not matches:
            desired.mkdir()
            folder = desired
        else:
            folder = matches[0]
            if folder.name != desired.name and not desired.exists():
                folder.rename(desired)
                folder = desired
        self._folders[chat.id] = folder
        opened = self._open.get(chat.id)
        if opened:
            opened.folder = folder
        self._write_chat_json(folder, chat)
        return folder

    def prepare(
        self,
        chat: ChatInfo,
        record: dict,
        policy: MediaPolicy,
        *,
        render: bool = True,
    ) -> PrepareResult:
        folder = self.chat_folder(chat)
        opened = self._index_for(chat.id, folder)
        key = event_key(record)
        is_new = key not in opened.keys
        downloads: list[dict] = []
        if record.get("kind") in {"message", "edit"}:
            for index, item in enumerate(record.get("media") or []):
                item["key"] = item.get("key") or f"{record.get('message_id')}:{index}"
                known = opened.media.get(item["key"])
                if _saved_on_disk(folder, known):
                    item["status"] = "saved"
                    item["path"] = known.get("path")
                    item["reason"] = None
                    continue
                if not policy.allow(item.get("role") or "file"):
                    item["status"] = "skipped"
                    item["reason"] = policy.skip_reason(item.get("role") or "file")
                    item["path"] = None
                    continue
                item["path"] = (known or {}).get("path") or self.allocate_media_path(folder, record, item)
                item["status"] = "pending"
                item["reason"] = None
                downloads.append(item)
        if is_new:
            self._append(folder, record)
            self._absorb(opened, record)
            opened.dirty.add(_day(record.get("date") or ""))
        if render:
            self.flush(chat.id)
        return PrepareResult(new=is_new, downloads=downloads if record.get("kind") in {"message", "edit"} else [], folder=folder)

    def mark_media(
        self,
        chat_id: int,
        record: dict,
        item: dict,
        *,
        status: str,
        reason: str | None = None,
        path: str | None = None,
        size: int | None = None,
        render: bool = True,
    ) -> None:
        folder = self._folders[chat_id]
        opened = self._index_for(chat_id, folder)
        event = {
            "kind": "file",
            "source": record.get("source"),
            "chat_id": chat_id,
            "message_id": record.get("message_id"),
            "date": record.get("date"),
            "key": item["key"],
            "role": item.get("role"),
            "file_name": item.get("file_name"),
            "status": status,
            "path": path,
            "reason": reason,
            "size": size if size is not None else item.get("size"),
        }
        current = opened.media.get(item["key"])
        if current and current.get("status") == status and current.get("path") == path and current.get("reason") == reason:
            return
        self._append(folder, event)
        self._absorb(opened, event)
        opened.dirty.add(_day(record.get("date") or ""))
        if render:
            self.flush(chat_id)

    def allocate_media_path(self, folder: Path, record: dict, item: dict) -> str:
        moment = datetime.fromisoformat(record["date"])
        day = moment.strftime("%Y/%m/%d")
        stamp = moment.strftime("%H%M%S")
        message_id = record.get("message_id") or 0
        filename = safe_filename(item.get("file_name") or "file")
        group = record.get("media_group_id")
        if group:
            relative = f"media/{day}/album_{safe_filename(str(group))}/{message_id}_{filename}"
        else:
            relative = f"media/{day}/{stamp}_{message_id}_{filename}"
        relative = _unused_relative(folder, relative)
        destination = folder / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        return relative

    def path_in_chat(self, chat_id: int, relative: str) -> Path:
        folder = self._folders[chat_id].resolve()
        destination = (folder / relative).resolve()
        if not destination.is_relative_to(folder):
            raise ValueError(f"Refusing to write outside the chat folder: {relative}")
        return destination

    def flush(self, chat_id: int | None = None) -> None:
        ids = [chat_id] if chat_id is not None else list(self._open)
        for cid in ids:
            opened = self._open.get(cid)
            if not opened:
                continue
            for day in sorted(opened.dirty):
                self._render_day(opened.folder, day)
            opened.dirty.clear()
        self.sync()

    def sync(self) -> None:
        for path in list(self._unsynced):
            if not path.exists():
                continue
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        self._unsynced.clear()

    def history_state(self, chat_id: int) -> dict:
        path = self._folders[chat_id] / "chat.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        state = data.get("history") or {}
        return {
            "complete": bool(state.get("complete")),
            "cursor": state.get("cursor"),
            "newest_seen": int(state.get("newest_seen") or 0),
        }

    def set_history_state(
        self,
        chat_id: int,
        *,
        cursor: int | None = None,
        newest_seen: int | None = None,
        complete: bool | None = None,
    ) -> None:
        path = self._folders[chat_id] / "chat.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"id": chat_id}
        state = data.setdefault("history", {})
        if cursor is not None:
            state["cursor"] = cursor
        if newest_seen is not None:
            state["newest_seen"] = newest_seen
        if complete is not None:
            state["complete"] = complete
        state["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        _atomic_json(path, data)

    def incomplete_media(self, policy: MediaPolicy, *, include_failed: bool) -> tuple[list[dict], int]:
        """Bot-file downloads still missing. The second value is history media with no bot file id."""
        jobs: list[dict] = []
        missing_file_id = 0
        chats = self.root / "chats"
        if not chats.exists():
            return jobs, missing_file_id
        for folder in sorted(path for path in chats.iterdir() if path.is_dir()):
            meta_path = folder / "chat.json"
            if not meta_path.exists():
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            chat = ChatInfo(
                id=int(meta["id"]),
                title=meta.get("title") or str(meta["id"]),
                type=meta.get("type") or "group",
                username=meta.get("username"),
            )
            self._folders[chat.id] = folder
            opened = self._index_for(chat.id, folder)
            messages = folder / "messages"
            if not messages.exists():
                continue
            seen: set[str] = set()
            for path in sorted(messages.glob("*.jsonl")):
                for record in _read_jsonl(path):
                    if record.get("kind") not in {"message", "edit"}:
                        continue
                    for item in record.get("media") or []:
                        key = item.get("key")
                        if not key or key in seen:
                            continue
                        seen.add(key)
                        current = opened.media.get(key) or item
                        status = current.get("status")
                        if _saved_on_disk(folder, current):
                            continue
                        if status == "saved":
                            status = "pending"
                        if not policy.allow(item.get("role") or "file"):
                            continue
                        if status == "failed" and not include_failed:
                            continue
                        if status not in {"pending", "failed", "skipped", None}:
                            continue
                        if not item.get("file_id"):
                            missing_file_id += 1
                            continue
                        jobs.append(
                            {
                                "chat": chat,
                                "record": record,
                                "item": {
                                    **item,
                                    "path": current.get("path") or item.get("path"),
                                    "status": status,
                                },
                            }
                        )
        return jobs, missing_file_id

    def _index_for(self, chat_id: int, folder: Path) -> OpenChat:
        opened = self._open.get(chat_id)
        if opened:
            opened.folder = folder
            return opened
        opened = OpenChat(folder=folder)
        messages = folder / "messages"
        if messages.exists():
            for path in sorted(messages.glob("*.jsonl")):
                for record in _read_jsonl(path):
                    self._absorb(opened, record)
        self._open[chat_id] = opened
        return opened

    def _absorb(self, opened: OpenChat, record: dict) -> None:
        kind = record.get("kind")
        if kind in {"message", "edit", "membership"}:
            opened.keys.add(event_key(record))
            for item in record.get("media") or []:
                key = item.get("key")
                if key and key not in opened.media:
                    opened.media[key] = {
                        "status": item.get("status"),
                        "path": item.get("path"),
                        "reason": item.get("reason"),
                    }
        elif kind == "file" and record.get("key"):
            opened.media[record["key"]] = {
                "status": record.get("status"),
                "path": record.get("path"),
                "reason": record.get("reason"),
            }

    def _append(self, folder: Path, record: dict) -> None:
        day = _day(record.get("date") or "")
        path = folder / "messages" / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
        self._unsynced.add(path)

    def _render_day(self, folder: Path, day: str) -> None:
        path = folder / "messages" / f"{day}.jsonl"
        if not path.exists():
            return
        text = render_day(_read_jsonl(path))
        _atomic_text(folder / "messages" / f"{day}.txt", text)

    def _write_chat_json(self, folder: Path, chat: ChatInfo) -> None:
        path = folder / "chat.json"
        current: dict = {}
        if path.exists():
            current = json.loads(path.read_text(encoding="utf-8"))
        title_changed = current.get("title") != chat.title
        username_changed = bool(chat.username) and current.get("username") != chat.username
        status_changed = bool(chat.bot_status) and current.get("bot_status") != chat.bot_status
        type_changed = bool(current) and current.get("type") != chat.type
        if current and not (title_changed or username_changed or status_changed or type_changed):
            return
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        history = list(current.get("title_history") or [])
        if not history or history[-1].get("title") != chat.title:
            history.append({"title": chat.title, "seen_at": now})
        data = {
            "id": chat.id,
            "type": chat.type or current.get("type") or "group",
            "title": chat.title,
            "username": chat.username if chat.username is not None else current.get("username"),
            "title_history": history,
            "bot_status": chat.bot_status or current.get("bot_status"),
            "history": current.get("history")
            or {"complete": False, "cursor": None, "newest_seen": 0},
        }
        _atomic_json(path, data)


def event_key(record: dict) -> str:
    kind = record.get("kind")
    if kind == "message":
        return f"m:{record.get('message_id')}"
    if kind == "edit":
        return f"e:{record.get('message_id')}:{record.get('date')}"
    if kind == "membership":
        return f"j:{record.get('date')}:{record.get('text')}"
    raise ValueError(f"Unknown record kind: {kind}")


def needs_catch_up(state: dict) -> bool:
    """A later run picks up messages that arrived after a previous walk."""
    return bool(state.get("cursor") or state.get("complete"))


def needs_backfill(state: dict) -> bool:
    return not bool(state.get("complete"))


def load_offset(root: Path) -> int | None:
    path = root / "state.json"
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8")).get("offset")
    return int(value) if value is not None else None


def save_offset(root: Path, offset: int) -> None:
    _atomic_json(root / "state.json", {"offset": offset})


def _saved_on_disk(folder: Path, known: dict | None) -> bool:
    if not known or known.get("status") != "saved" or not known.get("path"):
        return False
    return (folder / known["path"]).is_file()


def _unused_relative(folder: Path, relative: str) -> str:
    destination = folder / relative
    if not destination.exists():
        return relative
    stem = destination.stem
    suffix = destination.suffix
    parent = Path(relative).parent.as_posix()
    number = 2
    while True:
        name = f"{stem}_{number}{suffix}"
        candidate = f"{parent}/{name}" if parent != "." else name
        if not (folder / candidate).exists():
            return candidate
        number += 1


def _day(value: str) -> str:
    if len(value) >= 10:
        return value[:10]
    return "unknown-date"


def _read_jsonl(path: Path) -> list[dict]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            say(f"Skipping unreadable line {path}:{line_number}")
    return records


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def remove_empty_parents(start: Path, stop: Path) -> None:
    current = start
    while current != stop and current.is_relative_to(stop):
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _atomic_json(path: Path, data: dict) -> None:
    _atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
