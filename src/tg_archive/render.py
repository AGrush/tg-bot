from __future__ import annotations

from datetime import datetime


def display_time(value: str) -> str:
    return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M:%S")


def sender_label(record: dict) -> str:
    name = record.get("sender_name") or "unknown"
    username = record.get("sender_username")
    if username:
        return f"{name} (@{username})"
    return name


def render_day(events: list[dict]) -> str:
    overrides: dict[str, dict] = {}
    timeline: list[dict] = []
    for event in events:
        kind = event.get("kind")
        if kind == "file" and event.get("key"):
            overrides[event["key"]] = event
        elif kind in {"message", "edit", "membership"}:
            timeline.append(event)
    timeline.sort(key=_sort_key)

    blocks: list[str] = []
    index = 0
    while index < len(timeline):
        item = timeline[index]
        group_id = item.get("media_group_id") if item.get("kind") == "message" else None
        if group_id:
            group = [item]
            nxt = index + 1
            while nxt < len(timeline):
                candidate = timeline[nxt]
                if candidate.get("kind") != "message" or candidate.get("media_group_id") != group_id:
                    break
                group.append(candidate)
                nxt += 1
            blocks.append(_format_messages(group, overrides))
            index = nxt
            continue
        if item.get("kind") == "edit":
            blocks.append(_format_edit(item))
        elif item.get("kind") == "membership":
            blocks.append(_format_membership(item))
        else:
            blocks.append(_format_messages([item], overrides))
        index += 1
    if not blocks:
        return ""
    return "\n\n".join(blocks) + "\n"


def _sort_key(record: dict) -> tuple:
    kind_order = 1 if record.get("kind") == "edit" else 0
    return (record.get("date") or "", record.get("message_id") or 0, kind_order)


def _format_messages(group: list[dict], overrides: dict[str, dict]) -> str:
    first = group[0]
    lines = [f"[{display_time(first['date'])}] {sender_label(first)} {_id_label(group)}"]
    if first.get("thread_id"):
        lines.append(f"topic {first['thread_id']}")
    reply_id = first.get("reply_to_message_id")
    if reply_id and reply_id != first.get("thread_id"):
        lines.append(f"reply to #{reply_id}")
    if first.get("forwarded_from"):
        lines.append(f"forwarded from {first['forwarded_from']}")

    saved: list[tuple[str, str]] = []
    notes: list[str] = []
    for record in group:
        for item in _effective_media(record, overrides):
            role = item.get("role") or "file"
            status = item.get("status")
            path = item.get("path")
            if status == "saved" and path:
                saved.append((role, path.replace("\\", "/")))
            elif status == "skipped":
                notes.append(f"{role} not downloaded")
            elif status == "failed":
                notes.append(f"download failed: {item.get('reason') or 'unknown error'}")
            elif status == "pending":
                notes.append("download did not finish")
    if len(group) > 1 and saved:
        folder = saved[0][1].rsplit("/", 1)[0]
        lines.append(f"album: {folder}/")
        for _role, path in saved:
            lines.append(f"  {path.rsplit('/', 1)[-1]}")
    else:
        for role, path in saved:
            prefix = "image" if role == "image" else "file"
            lines.append(f"{prefix}: {path}")
    seen: set[str] = set()
    for note in notes:
        if note not in seen:
            seen.add(note)
            lines.append(note)

    texts: list[str] = []
    for record in group:
        text = (record.get("text") or "").strip()
        if text and text not in texts:
            texts.append(text)
    if texts:
        lines.append("\n".join(texts))
    return "\n".join(lines)


def _format_edit(record: dict) -> str:
    lines = [f"[{display_time(record['date'])}] edited #{record.get('message_id')} · {sender_label(record)}"]
    text = (record.get("text") or "").strip()
    if text:
        lines.append(text)
    return "\n".join(lines)


def _format_membership(record: dict) -> str:
    return f"[{display_time(record['date'])}] — {record.get('text') or 'membership change'}"


def _id_label(group: list[dict]) -> str:
    ids = [record.get("message_id") for record in group if record.get("message_id") is not None]
    if not ids:
        return ""
    edited = " (edited)" if len(group) == 1 and group[0].get("edited_at") else ""
    if len(ids) == 1:
        return f"#{ids[0]}{edited}"
    if ids == list(range(ids[0], ids[0] + len(ids))):
        return f"#{ids[0]}-#{ids[-1]}"
    return ", ".join(f"#{value}" for value in ids)


def _effective_media(record: dict, overrides: dict[str, dict]) -> list[dict]:
    merged: list[dict] = []
    for item in record.get("media") or []:
        current = dict(item)
        override = overrides.get(item.get("key"))
        if override:
            for field in ("status", "path", "reason"):
                if field in override:
                    current[field] = override[field]
        merged.append(current)
    return merged
