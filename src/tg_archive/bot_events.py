from __future__ import annotations

from datetime import datetime, timezone

from tg_archive.policy import role_for
from tg_archive.store import ChatInfo


def normalize_update(update: dict) -> list[tuple[ChatInfo, dict]]:
    found: list[tuple[ChatInfo, dict]] = []
    for field, kind in (
        ("message", "message"),
        ("edited_message", "edit"),
        ("channel_post", "message"),
        ("edited_channel_post", "edit"),
    ):
        payload = update.get(field)
        if payload:
            found.append(_from_message(payload, kind=kind, update=update))
    member = update.get("my_chat_member")
    if member:
        found.append(_from_membership(member, update))
    return found


def _from_message(message: dict, *, kind: str, update: dict) -> tuple[ChatInfo, dict]:
    chat = message.get("chat") or {}
    info = ChatInfo(
        id=int(chat["id"]),
        title=_chat_title(chat),
        type=chat.get("type") or "private",
        username=chat.get("username"),
    )
    when = message.get("edit_date") if kind == "edit" and message.get("edit_date") else message.get("date")
    sender_id, sender_name, username = _sender(message)
    text = message.get("text") or message.get("caption") or ""
    service = _service_text(message)
    if service:
        text = service if not text else f"{text}\n{service}"
    record = {
        "kind": kind,
        "source": "bot",
        "chat_id": info.id,
        "message_id": message.get("message_id"),
        "date": _unix_to_local(when or message.get("date") or 0),
        "sender_id": sender_id,
        "sender_name": sender_name,
        "sender_username": username,
        "text": text,
        "media": [] if service else _media(message),
        "media_group_id": _group_id(message.get("media_group_id")),
        "reply_to_message_id": _reply_id(message),
        "thread_id": message.get("message_thread_id") if message.get("is_topic_message") else None,
        "forwarded_from": _forward_label(message),
        "update_id": update.get("update_id"),
        "raw": update,
    }
    return info, record


def _from_membership(member: dict, update: dict) -> tuple[ChatInfo, dict]:
    chat = member.get("chat") or {}
    status = (member.get("new_chat_member") or {}).get("status") or "unknown"
    info = ChatInfo(
        id=int(chat["id"]),
        title=_chat_title(chat),
        type=chat.get("type") or "private",
        username=chat.get("username"),
        bot_status=status,
    )
    user = member.get("from") or {}
    sender_id, sender_name, username = _user_fields(user)
    record = {
        "kind": "membership",
        "source": "bot",
        "chat_id": info.id,
        "message_id": None,
        "date": _unix_to_local(member.get("date") or 0),
        "sender_id": sender_id,
        "sender_name": sender_name,
        "sender_username": username,
        "text": f"bot status is now {status}",
        "media": [],
        "media_group_id": None,
        "reply_to_message_id": None,
        "thread_id": None,
        "forwarded_from": None,
        "update_id": update.get("update_id"),
        "raw": update,
    }
    return info, record


def _media(message: dict) -> list[dict]:
    items: list[dict] = []
    photos = message.get("photo") or []
    if photos:
        largest = max(photos, key=lambda item: item.get("file_size") or 0)
        items.append(
            _file_item(
                kind="photo",
                file_name="photo.jpg",
                mime="image/jpeg",
                size=largest.get("file_size"),
                file_id=largest.get("file_id"),
                unique_id=largest.get("file_unique_id"),
            )
        )
    named = (
        ("animation", "animation.mp4"),
        ("video", "video.mp4"),
        ("audio", "audio.mp3"),
        ("voice", "voice.ogg"),
        ("video_note", "video_note.mp4"),
        ("sticker", "sticker.webp"),
        ("document", "document"),
    )
    for kind, fallback in named:
        obj = message.get(kind)
        if not obj:
            continue
        mime = obj.get("mime_type")
        file_name = obj.get("file_name") or _default_name(kind, mime, obj, fallback)
        items.append(
            _file_item(
                kind=kind,
                file_name=file_name,
                mime=mime,
                size=obj.get("file_size"),
                file_id=obj.get("file_id"),
                unique_id=obj.get("file_unique_id"),
            )
        )
    return items


def _file_item(*, kind: str, file_name: str, mime: str | None, size: int | None, file_id: str | None, unique_id: str | None) -> dict:
    return {
        "role": role_for(kind, mime),
        "kind": kind,
        "file_name": file_name,
        "mime": mime,
        "size": size,
        "file_id": file_id,
        "file_unique_id": unique_id,
    }


def _default_name(kind: str, mime: str | None, obj: dict, fallback: str) -> str:
    if kind == "sticker" and obj.get("is_video"):
        return "sticker.webm"
    if kind == "sticker" and obj.get("is_animated"):
        return "sticker.tgs"
    if kind == "animation":
        return "animation.gif" if mime == "image/gif" else "animation.mp4"
    return fallback


def _service_text(message: dict) -> str | None:
    if message.get("new_chat_members"):
        names = [_person(user) for user in message["new_chat_members"]]
        return "joined: " + ", ".join(names)
    if message.get("left_chat_member"):
        return "left: " + _person(message["left_chat_member"])
    if message.get("new_chat_title"):
        return f"changed the group title to {message['new_chat_title']}"
    if message.get("new_chat_photo"):
        return "changed the group photo"
    if message.get("delete_chat_photo"):
        return "removed the group photo"
    if message.get("group_chat_created") or message.get("supergroup_chat_created"):
        return "created the group"
    if message.get("channel_chat_created"):
        return "created the channel"
    if message.get("pinned_message"):
        return "pinned a message"
    topic = message.get("forum_topic_created")
    if topic:
        return f"created topic {topic.get('name', '')}".strip()
    if message.get("forum_topic_edited"):
        return "edited a topic"
    if message.get("migrate_to_chat_id"):
        return f"this group moved to chat id {message['migrate_to_chat_id']}"
    if message.get("migrate_from_chat_id"):
        return f"migrated from chat id {message['migrate_from_chat_id']}"
    return None


def _sender(message: dict) -> tuple[int | None, str, str | None]:
    user = message.get("from")
    if user:
        return _user_fields(user)
    sender_chat = message.get("sender_chat")
    if sender_chat:
        return sender_chat.get("id"), sender_chat.get("title") or "channel", sender_chat.get("username")
    signature = message.get("author_signature")
    chat = message.get("chat") or {}
    if signature:
        return None, signature, None
    return None, chat.get("title") or "channel", chat.get("username")


def _user_fields(user: dict) -> tuple[int | None, str, str | None]:
    return user.get("id"), _person(user), user.get("username")


def _person(user: dict) -> str:
    name = " ".join(part for part in [user.get("first_name"), user.get("last_name")] if part).strip()
    return name or user.get("username") or user.get("title") or "unknown"


def _chat_title(chat: dict) -> str:
    return (
        chat.get("title")
        or chat.get("username")
        or " ".join(part for part in [chat.get("first_name"), chat.get("last_name")] if part).strip()
        or str(chat.get("id"))
    )


def _forward_label(message: dict) -> str | None:
    if message.get("forward_sender_name"):
        return message["forward_sender_name"]
    if message.get("forward_from"):
        return _person(message["forward_from"])
    if message.get("forward_from_chat"):
        return message["forward_from_chat"].get("title") or message["forward_from_chat"].get("username")
    origin = message.get("forward_origin") or {}
    if origin.get("sender_user_name"):
        return origin["sender_user_name"]
    if origin.get("sender_user"):
        return _person(origin["sender_user"])
    if origin.get("sender_chat"):
        return origin["sender_chat"].get("title")
    if origin.get("chat"):
        return origin["chat"].get("title")
    return None


def _reply_id(message: dict) -> int | None:
    reply = message.get("reply_to_message") or {}
    return reply.get("message_id")


def _group_id(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _unix_to_local(timestamp: int) -> str:
    moment = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone()
    return moment.isoformat(timespec="seconds")
