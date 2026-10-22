from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest
from telethon.tl.types import (
    Document,
    DocumentAttributeFilename,
    MessageMediaContact,
    MessageMediaDice,
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaWebPage,
    Photo,
    PhotoSize,
)

from tg_archive.bot import TOO_BIG, hosted_too_big
from tg_archive.bot_events import normalize_update
from tg_archive.cli import build_parser
from tg_archive.config import load_dotenv, parse_allowed, parse_bool
from tg_archive.media import describe_action, describe_media, extra_text
from tg_archive.names import safe_filename, slugify
from tg_archive.policy import MediaPolicy, role_for
from tg_archive.store import Archive, ChatInfo, load_offset, needs_backfill, needs_catch_up, save_offset


def text_message(**overrides) -> dict:
    record = {
        "kind": "message",
        "source": "bot",
        "chat_id": -1001,
        "message_id": 42,
        "date": "2026-10-22T14:30:12+02:00",
        "sender_id": 7,
        "sender_name": "Ada",
        "sender_username": "ada",
        "text": "hello",
        "media": [],
        "media_group_id": None,
        "reply_to_message_id": None,
        "thread_id": None,
        "forwarded_from": None,
    }
    record.update(overrides)
    return record


def photo_item(**overrides) -> dict:
    item = {
        "role": "image",
        "kind": "photo",
        "file_name": "photo.jpg",
        "mime": "image/jpeg",
        "size": 100,
        "file_id": "photo-file",
        "file_unique_id": "photo-unique",
    }
    item.update(overrides)
    return item


def test_names_keep_readable_text_and_drop_directories():
    assert slugify("Design Reviews") == "Design-Reviews"
    assert slugify("Чтение группы") == "Чтение-группы"
    assert slugify("///") == "chat"
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("") == "file"
    assert "/" not in safe_filename("a/b.pdf")


def test_roles():
    assert role_for("photo", "image/jpeg") == "image"
    assert role_for("animation", "video/mp4") == "image"
    assert role_for("document", "image/png") == "image"
    assert role_for("document", "application/pdf") == "file"
    assert role_for("sticker", "image/webp") == "file"
    assert role_for("video", "video/mp4") == "file"
    assert role_for("voice", "audio/ogg") == "file"


def test_hosted_limit():
    limit = 20 * 1024 * 1024
    assert hosted_too_big(limit + 1, "https://api.telegram.org") is True
    assert hosted_too_big(limit, "https://api.telegram.org") is False
    assert hosted_too_big(None, "https://api.telegram.org") is False
    assert hosted_too_big(limit + 1, "http://127.0.0.1:8081") is False
    assert TOO_BIG


def test_history_resume_plan():
    assert needs_catch_up({}) is False
    assert needs_backfill({}) is True
    assert needs_catch_up({"cursor": 10, "newest_seen": 50}) is True
    assert needs_backfill({"cursor": 10, "complete": False}) is True
    assert needs_catch_up({"complete": True, "newest_seen": 0}) is True
    assert needs_backfill({"complete": True}) is False


def test_text_lands_in_one_chat_folder(tmp_path):
    archive = Archive(tmp_path)
    chat = ChatInfo(id=-1001, title="Design Reviews", type="supergroup")
    other = ChatInfo(id=-1002, title="Design Reviews", type="supergroup")
    first = archive.prepare(chat, text_message(), MediaPolicy(True, True))
    second = archive.prepare(other, text_message(chat_id=-1002, message_id=7, text="other"), MediaPolicy(True, True))
    assert first.new is True
    assert first.folder.name == "-1001__Design-Reviews"
    assert second.folder.name == "-1002__Design-Reviews"
    assert "hello" in (first.folder / "messages" / "2026-10-22.txt").read_text(encoding="utf-8")
    assert "other" in (second.folder / "messages" / "2026-10-22.txt").read_text(encoding="utf-8")
    again = archive.prepare(chat, text_message(), MediaPolicy(True, True))
    assert again.new is False
    lines = (first.folder / "messages" / "2026-10-22.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_title_change_renames_folder_and_keeps_messages(tmp_path):
    archive = Archive(tmp_path)
    chat = ChatInfo(id=-1001, title="Design Reviews", type="supergroup")
    archive.prepare(chat, text_message(), MediaPolicy(True, True))
    renamed = ChatInfo(id=-1001, title="Design", type="supergroup")
    result = archive.prepare(renamed, text_message(message_id=43, text="next"), MediaPolicy(True, True))
    assert result.folder.name == "-1001__Design"
    assert not (tmp_path / "chats" / "-1001__Design-Reviews").exists()
    body = (result.folder / "messages" / "2026-10-22.txt").read_text(encoding="utf-8")
    assert "hello" in body and "next" in body
    meta = json.loads((result.folder / "chat.json").read_text(encoding="utf-8"))
    assert [item["title"] for item in meta["title_history"]] == ["Design Reviews", "Design"]


def test_images_and_files_can_be_filled_in_later(tmp_path):
    archive = Archive(tmp_path)
    chat = ChatInfo(id=-1001, title="Design", type="supergroup")
    record = text_message(
        text="see this",
        media=[photo_item(file_name="../../etc/passwd")],
    )
    skipped = archive.prepare(chat, record, MediaPolicy(images=False, files=False))
    assert skipped.downloads == []
    saved_text = (skipped.folder / "messages" / "2026-10-22.txt").read_text(encoding="utf-8")
    assert "image not downloaded" in saved_text
    assert "passwd" not in saved_text

    fresh = Archive(tmp_path)
    jobs, missing = fresh.incomplete_media(MediaPolicy(True, False), include_failed=False)
    assert missing == 0
    assert len(jobs) == 1
    assert jobs[0]["item"]["file_id"] == "photo-file"

    filled = fresh.prepare(chat, text_message(text="see this", media=[photo_item()]), MediaPolicy(True, False))
    assert filled.new is False
    assert len(filled.downloads) == 1
    relative = filled.downloads[0]["path"]
    assert relative.endswith("143012_42_photo.jpg")
    assert ".." not in relative
    destination = filled.folder / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"jpeg")
    fresh.mark_media(chat.id, text_message(), filled.downloads[0], status="saved", path=relative, size=4)
    body = (filled.folder / "messages" / "2026-10-22.txt").read_text(encoding="utf-8")
    assert f"image: {relative}" in body
    assert "not downloaded" not in body
    assert fresh.incomplete_media(MediaPolicy(True, True), include_failed=True) == ([], 0)
    destination.unlink()
    again, still_missing = fresh.incomplete_media(MediaPolicy(True, True), include_failed=False)
    assert len(again) == 1
    assert still_missing == 0


def test_files_stay_separate_from_images(tmp_path):
    archive = Archive(tmp_path)
    chat = ChatInfo(id=-1001, title="Design", type="supergroup")
    record = text_message(
        message_id=9,
        media=[
            {
                "role": "file",
                "kind": "document",
                "file_name": "spec.pdf",
                "mime": "application/pdf",
                "size": 20,
                "file_id": "pdf",
                "file_unique_id": "pdf-1",
            }
        ],
    )
    images_only = archive.prepare(chat, record, MediaPolicy(images=True, files=False))
    assert images_only.downloads == []
    assert "file not downloaded" in (images_only.folder / "messages" / "2026-10-22.txt").read_text(encoding="utf-8")
    files_on = Archive(tmp_path).prepare(chat, record, MediaPolicy(images=False, files=True))
    assert len(files_on.downloads) == 1
    assert files_on.downloads[0]["path"].endswith("143012_9_spec.pdf")


def test_album_folder_and_transcript(tmp_path):
    archive = Archive(tmp_path)
    chat = ChatInfo(id=-1001, title="Design", type="supergroup")
    policy = MediaPolicy(False, False)
    archive.prepare(
        chat,
        text_message(message_id=50, media_group_id="99", text="two photos", media=[photo_item()]),
        policy,
    )
    archive.prepare(
        chat,
        text_message(message_id=51, media_group_id="99", text="", media=[photo_item(file_id="p2")]),
        policy,
    )
    body = (tmp_path / "chats" / "-1001__Design" / "messages" / "2026-10-22.txt").read_text(encoding="utf-8")
    assert "#50-#51" in body
    assert "two photos" in body


def test_edit_reply_and_forward_render(tmp_path):
    archive = Archive(tmp_path)
    chat = ChatInfo(id=-1001, title="Design", type="supergroup")
    archive.prepare(
        chat,
        text_message(reply_to_message_id=3, forwarded_from="Bob", thread_id=8),
        MediaPolicy(True, True),
    )
    archive.prepare(
        chat,
        text_message(kind="edit", message_id=42, date="2026-10-22T14:31:01+02:00", text="hello again"),
        MediaPolicy(True, True),
    )
    body = (tmp_path / "chats" / "-1001__Design" / "messages" / "2026-10-22.txt").read_text(encoding="utf-8")
    assert "reply to #3" in body
    assert "forwarded from Bob" in body
    assert "topic 8" in body
    assert "edited #42" in body
    assert "hello again" in body


def test_offset_roundtrip(tmp_path):
    assert load_offset(tmp_path) is None
    save_offset(tmp_path, 41)
    assert load_offset(tmp_path) == 41


def test_bot_update_shapes():
    update = {
        "update_id": 5,
        "message": {
            "message_id": 42,
            "date": int(datetime(2026, 10, 22, tzinfo=timezone.utc).timestamp()),
            "chat": {"id": -1001, "title": "Design", "type": "supergroup"},
            "from": {"id": 7, "first_name": "Ada", "username": "ada"},
            "text": "hello",
            "photo": [
                {"file_id": "small", "file_unique_id": "s", "file_size": 10},
                {"file_id": "big", "file_unique_id": "b", "file_size": 90},
            ],
        },
    }
    chat, record = normalize_update(update)[0]
    assert chat.id == -1001
    assert chat.title == "Design"
    assert record["text"] == "hello"
    assert record["media"][0]["file_id"] == "big"
    assert record["media"][0]["role"] == "image"
    assert record["sender_username"] == "ada"

    document = {
        "update_id": 6,
        "message": {
            "message_id": 43,
            "date": 1_700_000_000,
            "chat": {"id": -1001, "title": "Design", "type": "supergroup"},
            "from": {"id": 7, "first_name": "Ada"},
            "caption": "spec",
            "document": {
                "file_id": "doc",
                "file_unique_id": "d",
                "file_name": "spec.pdf",
                "mime_type": "application/pdf",
                "file_size": 12,
            },
        },
    }
    _, doc_record = normalize_update(document)[0]
    assert doc_record["text"] == "spec"
    assert doc_record["media"][0]["role"] == "file"

    sticker = {
        "update_id": 7,
        "message": {
            "message_id": 44,
            "date": 1_700_000_000,
            "chat": {"id": -1001, "title": "Design", "type": "supergroup"},
            "sticker": {"file_id": "st", "file_unique_id": "su", "is_animated": True, "mime_type": "application/x-tgsticker"},
        },
    }
    _, sticker_record = normalize_update(sticker)[0]
    assert sticker_record["media"][0]["role"] == "file"
    assert sticker_record["media"][0]["file_name"] == "sticker.tgs"

    edited = {
        "update_id": 8,
        "edited_message": {
            "message_id": 42,
            "date": 1_700_000_000,
            "edit_date": 1_700_000_100,
            "chat": {"id": -1001, "title": "Design", "type": "supergroup"},
            "from": {"id": 7, "first_name": "Ada"},
            "text": "changed",
        },
    }
    _, edit_record = normalize_update(edited)[0]
    assert edit_record["kind"] == "edit"
    assert edit_record["text"] == "changed"

    joined = {
        "update_id": 9,
        "message": {
            "message_id": 45,
            "date": 1_700_000_000,
            "chat": {"id": -1001, "title": "Design", "type": "supergroup"},
            "from": {"id": 7, "first_name": "Ada"},
            "new_chat_members": [{"id": 8, "first_name": "Bea"}],
        },
    }
    _, join_record = normalize_update(joined)[0]
    assert join_record["text"] == "joined: Bea"
    assert join_record["media"] == []

    member = {
        "update_id": 10,
        "my_chat_member": {
            "chat": {"id": -1001, "title": "Design", "type": "supergroup"},
            "from": {"id": 7, "first_name": "Ada"},
            "date": 1_700_000_000,
            "old_chat_member": {"status": "left"},
            "new_chat_member": {"status": "member"},
        },
    }
    info, membership = normalize_update(member)[0]
    assert info.bot_status == "member"
    assert membership["text"] == "bot status is now member"


def test_history_media_classification():
    photo = MessageMediaPhoto(
        photo=Photo(
            id=5,
            access_hash=6,
            file_reference=b"",
            date=None,
            sizes=[PhotoSize("x", 10, 10, 20)],
            dc_id=2,
        )
    )
    described = describe_media(photo)
    assert described[0]["role"] == "image"
    assert described[0]["file_name"] == "photo.jpg"
    assert described[0]["file_id"] is None

    png = MessageMediaDocument(
        document=Document(
            id=1,
            access_hash=2,
            file_reference=b"",
            date=None,
            mime_type="image/png",
            size=10,
            dc_id=2,
            attributes=[DocumentAttributeFilename("pic.png")],
        )
    )
    assert describe_media(png)[0]["role"] == "image"
    pdf = MessageMediaDocument(
        document=Document(
            id=3,
            access_hash=4,
            file_reference=b"",
            date=None,
            mime_type="application/pdf",
            size=10,
            dc_id=2,
            attributes=[DocumentAttributeFilename("spec.pdf")],
        )
    )
    assert describe_media(pdf)[0]["role"] == "file"
    assert describe_media(pdf)[0]["file_name"] == "spec.pdf"
    assert describe_media(MessageMediaWebPage(webpage=None)) == []
    assert extra_text(MessageMediaDice(value=4, emoticon="🎲")) == "dice: 🎲 4"
    assert "Ada" in extra_text(MessageMediaContact("1", "Ada", "Lovelace", "", 7))


def test_action_text():
    class MessageActionPinMessage:
        pass

    class MessageActionChatEditTitle:
        title = "New name"

    assert describe_action(MessageActionPinMessage()) == "pinned a message"
    assert describe_action(MessageActionChatEditTitle()) == "changed the group title to New name"
    assert describe_action(None) is None


def test_env_and_parser(tmp_path, monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("DOWNLOAD_IMAGES", raising=False)
    env = tmp_path / ".env"
    env.write_text('BOT_TOKEN=from-file\nDOWNLOAD_IMAGES=false\nALLOWED_CHAT_IDS=-1001, -1002\n', encoding="utf-8")
    monkeypatch.setenv("BOT_TOKEN", "already")
    load_dotenv(env)
    assert os.environ["BOT_TOKEN"] == "already"
    assert os.environ["DOWNLOAD_IMAGES"] == "false"
    assert parse_bool(None, default=True) is True
    assert parse_bool("false", default=True) is False
    assert parse_allowed("-1001, -1002") == {-1001, -1002}
    assert parse_allowed("  ") is None
    with pytest.raises(SystemExit):
        parse_allowed("nope")
    history = build_parser().parse_args(["history", "--chat", "-1001"])
    assert history.images is False
    assert history.files is False
    assert history.chat == ["-1001"]
    run = build_parser().parse_args(["run", "--no-files"])
    assert run.images is None
    assert run.files is False


def test_distinct_chat_prefixes(tmp_path):
    archive = Archive(tmp_path)
    archive.prepare(ChatInfo(-100, "A", "group"), text_message(chat_id=-100), MediaPolicy(True, True))
    archive.prepare(ChatInfo(-1001, "B", "supergroup"), text_message(chat_id=-1001), MediaPolicy(True, True))
    names = sorted(path.name for path in (tmp_path / "chats").iterdir())
    assert names == ["-1001__B", "-100__A"]
