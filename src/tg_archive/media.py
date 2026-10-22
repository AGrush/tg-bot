from __future__ import annotations

import mimetypes
import re

from telethon.tl import types
from telethon.tl.custom.file import File

from tg_archive.policy import role_for

_ACTIONS = {
    "MessageActionChatAddUser": "added a member",
    "MessageActionChatJoinedByLink": "joined via invite link",
    "MessageActionChatJoinedByRequest": "joined the group",
    "MessageActionChatDeleteUser": "removed a member",
    "MessageActionChatEditPhoto": "changed the group photo",
    "MessageActionChatDeletePhoto": "removed the group photo",
    "MessageActionChatCreate": "created the group",
    "MessageActionChannelCreate": "created the channel",
    "MessageActionPinMessage": "pinned a message",
    "MessageActionHistoryClear": "cleared the chat history",
    "MessageActionContactSignUp": "joined Telegram",
    "MessageActionGroupCall": "started a call",
    "MessageActionInviteToGroupCall": "invited people to a call",
}


def describe_action(action: object | None) -> str | None:
    if action is None:
        return None
    name = type(action).__name__
    if name in _ACTIONS:
        return _ACTIONS[name]
    title = getattr(action, "title", None)
    if name == "MessageActionChatEditTitle":
        return f"changed the group title to {title}".strip()
    if name == "MessageActionTopicCreate":
        return f"created topic {title}".strip()
    if name == "MessageActionTopicEdit":
        return "edited a topic"
    if name == "MessageActionCustomAction":
        return getattr(action, "message", None) or "service message"
    if name == "MessageActionChatMigrateTo":
        channel_id = getattr(action, "channel_id", None)
        if channel_id:
            return f"this group moved to a supergroup ({channel_id})"
        return "this group moved to a supergroup"
    if name == "MessageActionChannelMigrateFrom":
        return "migrated from a basic group"
    readable = name.removeprefix("MessageAction")
    readable = re.sub(r"(?<!^)(?=[A-Z])", " ", readable).strip().lower()
    return readable or "service message"


def extra_text(media: object | None) -> str | None:
    """Plain description for contacts, polls, locations, and dice. Links stay in the message text."""
    if isinstance(media, types.MessageMediaContact):
        name = " ".join(part for part in [media.first_name, media.last_name] if part).strip()
        return f"contact: {name} {media.phone_number}".strip()
    if isinstance(media, types.MessageMediaDice):
        return f"dice: {media.emoticon} {media.value}".strip()
    if isinstance(media, (types.MessageMediaGeo, types.MessageMediaGeoLive)):
        geo = media.geo
        lat = getattr(geo, "lat", None)
        lon = getattr(geo, "long", None)
        if lat is None:
            return "location"
        return f"location: {lat}, {lon}"
    if isinstance(media, types.MessageMediaVenue):
        return f"venue: {media.title} — {media.address}"
    if isinstance(media, types.MessageMediaPoll):
        return f"poll: {_plain(media.poll.question)}"
    return None


def describe_media(media: object | None) -> list[dict]:
    """Downloadable media on a user-account message. Link previews are ignored."""
    if isinstance(media, types.MessageMediaPhoto):
        photo = media.photo
        if not isinstance(photo, types.Photo):
            return []
        return [
            _item(
                kind="photo",
                file_name="photo.jpg",
                mime="image/jpeg",
                size=File(photo).size,
                unique_id=str(photo.id),
            )
        ]
    if isinstance(media, types.MessageMediaDocument):
        document = media.document
        if not isinstance(document, types.Document):
            return []
        kind = _document_kind(document, media)
        info = File(document)
        mime = info.mime_type or None
        return [
            _item(
                kind=kind,
                file_name=_filename(kind, mime, info.name),
                mime=mime,
                size=info.size,
                unique_id=str(document.id),
            )
        ]
    return []


def _item(*, kind: str, file_name: str, mime: str | None, size: int | None, unique_id: str) -> dict:
    return {
        "role": role_for(kind, mime),
        "kind": kind,
        "file_name": file_name,
        "mime": mime,
        "size": size,
        "file_id": None,
        "file_unique_id": unique_id,
    }


def _document_kind(document: types.Document, media: types.MessageMediaDocument) -> str:
    attrs = document.attributes or []
    if any(isinstance(attr, types.DocumentAttributeSticker) for attr in attrs):
        return "sticker"
    for attr in attrs:
        if isinstance(attr, types.DocumentAttributeAudio) and getattr(attr, "voice", False):
            return "voice"
        if isinstance(attr, types.DocumentAttributeVideo) and getattr(attr, "round_message", False):
            return "video_note"
    if any(isinstance(attr, types.DocumentAttributeAnimated) for attr in attrs):
        return "animation"
    if any(isinstance(attr, types.DocumentAttributeAudio) for attr in attrs):
        return "audio"
    if any(isinstance(attr, types.DocumentAttributeVideo) for attr in attrs):
        return "video"
    if getattr(media, "voice", None):
        return "voice"
    if getattr(media, "round", None):
        return "video_note"
    if getattr(media, "video", None):
        return "video"
    return "document"


def _filename(kind: str, mime: str | None, name: str | None) -> str:
    if name:
        return name
    if kind == "sticker":
        if mime == "application/x-tgsticker":
            return "sticker.tgs"
        if mime == "video/webm":
            return "sticker.webm"
        return "sticker.webp"
    if kind == "animation":
        return "animation.gif" if mime == "image/gif" else "animation.mp4"
    defaults = {
        "photo": "photo.jpg",
        "video": "video.mp4",
        "video_note": "video_note.mp4",
        "audio": "audio.mp3",
        "voice": "voice.ogg",
        "document": "document",
    }
    if kind == "document" and mime:
        ext = mimetypes.guess_extension(mime) or ""
        if ext == ".jpe":
            ext = ".jpg"
        return f"document{ext}"
    return defaults.get(kind, "file")


def _plain(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    text = getattr(value, "text", None)
    if isinstance(text, str):
        return text
    return str(value)
