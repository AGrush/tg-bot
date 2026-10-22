from __future__ import annotations

import asyncio
import getpass
from datetime import datetime, timezone
from pathlib import Path

from telethon import TelegramClient, utils
from telethon.errors import (
    ApiIdInvalidError,
    ChannelPrivateError,
    ChatAdminRequiredError,
    FloodWaitError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    RPCError,
)
from telethon.tl import types

from tg_archive.config import Settings
from tg_archive.logutil import log_error, say
from tg_archive.media import describe_action, describe_media, extra_text
from tg_archive.policy import MediaPolicy
from tg_archive.store import Archive, ChatInfo, needs_backfill, needs_catch_up, remove_empty_parents


async def login(settings: Settings) -> None:
    client = await connect_user(settings)
    try:
        await _show_me(client)
        say(f"Session saved in {settings.data_dir.resolve()}")
    finally:
        await client.disconnect()


async def list_chats(settings: Settings, *, include_dms: bool) -> None:
    client = await connect_user(settings)
    try:
        await _show_me(client)
        rows: list[tuple] = []
        async for dialog in client.iter_dialogs(ignore_migrated=True):
            kind = _kind_of_dialog(dialog)
            if kind == "private" and not include_dms:
                continue
            if kind == "other":
                continue
            username = getattr(dialog.entity, "username", None)
            rows.append((dialog.name.casefold(), dialog.id, kind, dialog.name, f"@{username}" if username else ""))
        rows.sort()
        if not rows:
            say("No groups or channels found on this account.")
            return
        say(f"{'id':<18} {'type':<12} name")
        for _, chat_id, kind, name, username in rows:
            suffix = f"  {username}" if username else ""
            say(f"{chat_id:<18} {kind:<12} {name}{suffix}")
        say("")
        say("Text only:")
        say("  tg-archive history --chat ID")
        say("Text and images:")
        say("  tg-archive history --chat ID --images")
        say("Text and files (documents, audio, video, voice, stickers):")
        say("  tg-archive history --chat ID --files")
        say("Text, images, and files:")
        say("  tg-archive history --chat ID --images --files")
    finally:
        await client.disconnect()


async def run_history(
    settings: Settings,
    chat_specs: list[str],
    all_groups: bool,
    policy: MediaPolicy,
    assume_yes: bool,
) -> None:
    if not chat_specs and not all_groups:
        raise SystemExit(
            "Pass a chat to download.\n"
            "  tg-archive history --chat ID\n"
            "  tg-archive history --chat ID --images\n"
            "  tg-archive history --chat ID --files\n"
            "  tg-archive history --chat ID --images --files\n"
            "Run `tg-archive chats` to list ids. Text is always saved. "
            "Images and other files are downloaded only when you pass those flags. "
            "Run the same command again later to fill in media you skipped."
        )
    archive = Archive(settings.data_dir)
    client = await connect_user(settings)
    try:
        targets = await _collect_targets(client, chat_specs, all_groups)
        if all_groups and not assume_yes:
            say(f"This will read {len(targets)} chats ({policy.label()}).")
            answer = input("Continue? [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                raise SystemExit("Cancelled.")
        for entity in targets:
            title = utils.get_display_name(entity) or str(utils.get_peer_id(entity))
            try:
                await backfill_chat(client, entity, settings, archive, policy)
            except (ChannelPrivateError, ChatAdminRequiredError, RPCError) as exc:
                say(f"Could not read {title}: {exc}")
    finally:
        archive.flush()
        await client.disconnect()


async def connect_user(settings: Settings) -> TelegramClient:
    settings.require_user()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(
        str(settings.session_path),
        settings.api_id,
        settings.api_hash,
        flood_sleep_threshold=60,
    )
    await client.connect()
    if await client.is_user_authorized():
        return client
    phone = settings.phone or input("Phone number, international format (example +15551234567): ").strip()
    try:
        await client.start(
            phone=phone,
            code_callback=lambda: input("Code from Telegram: ").strip(),
            password=lambda: getpass.getpass("Two-step password: "),
        )
    except ApiIdInvalidError as exc:
        raise SystemExit("API_ID or API_HASH was rejected. Check the values from my.telegram.org.") from exc
    except PhoneNumberInvalidError as exc:
        raise SystemExit("That phone number was not accepted. Use international format, for example +15551234567.") from exc
    except PhoneCodeInvalidError as exc:
        raise SystemExit("That code was not accepted. Run the command again and use the newest code.") from exc
    return client


async def backfill_chat(client, entity, settings: Settings, archive: Archive, policy: MediaPolicy) -> None:
    info = _chat_info(entity)
    if not settings.chat_allowed(info.id):
        say(f"Skipping {info.title} ({info.id}): not in ALLOWED_CHAT_IDS.")
        return
    folder = archive.chat_folder(info)
    state = archive.history_state(info.id)
    stats = {"checked": 0, "new": 0, "images": 0, "files": 0}
    interrupted = False
    completed = False
    say("")
    say(f"{info.title} ({info.id})")
    say(f"Saving {policy.label()}.")
    say(f"Folder: {folder}")
    try:
        if needs_catch_up(state):
            await _walk(
                client,
                entity,
                info,
                archive,
                policy,
                stats,
                state,
                floor=int(state.get("newest_seen") or 0),
                ceiling=0,
                track_cursor=False,
            )
        if needs_backfill(state):
            await _walk(
                client,
                entity,
                info,
                archive,
                policy,
                stats,
                state,
                floor=0,
                ceiling=int(state.get("cursor") or 0),
                track_cursor=True,
            )
            state["complete"] = True
            if state.get("newest_seen"):
                await _walk(
                    client,
                    entity,
                    info,
                    archive,
                    policy,
                    stats,
                    state,
                    floor=int(state.get("newest_seen") or 0),
                    ceiling=0,
                    track_cursor=False,
                )
        completed = True
    except asyncio.CancelledError:
        interrupted = True
        raise
    finally:
        _checkpoint(archive, info.id, state)
        if interrupted:
            say("Progress is saved. Run the same history command again to continue.")
        elif completed:
            say(
                f"Finished {info.title}: {stats['checked']} checked, {stats['new']} new, "
                f"{stats['images']} images downloaded, {stats['files']} files downloaded."
            )


async def _walk(client, entity, info, archive, policy, stats, state, *, floor: int, ceiling: int, track_cursor: bool) -> None:
    # Telethon walks newest to oldest. min_id keeps messages above a floor.
    # max_id excludes that id and anything newer, which is how a stopped walk resumes.
    while True:
        kwargs: dict = {"wait_time": 1}
        if floor:
            kwargs["min_id"] = floor
        if ceiling:
            kwargs["max_id"] = ceiling
        last_id = None
        try:
            async for message in client.iter_messages(entity, **kwargs):
                try:
                    await _handle_message(message, info, archive, policy, stats)
                except FloodWaitError:
                    raise
                except Exception as exc:
                    say(f"  skipped message {getattr(message, 'id', '?')}: {exc}")
                    log_error(archive.root, f"history {info.id} message {getattr(message, 'id', '?')}: {exc}")
                last_id = message.id
                if message.id > int(state.get("newest_seen") or 0):
                    state["newest_seen"] = message.id
                if track_cursor:
                    state["cursor"] = message.id
                stats["checked"] += 1
                if stats["checked"] % 50 == 0:
                    _checkpoint(archive, info.id, state)
                    say(
                        f"  {stats['checked']} checked, {stats['new']} new, "
                        f"{stats['images']} images downloaded, {stats['files']} files downloaded"
                    )
            return
        except FloodWaitError as exc:
            _checkpoint(archive, info.id, state)
            say(f"Telegram asked for a {exc.seconds}s pause.")
            await asyncio.sleep(exc.seconds + 1)
            if last_id:
                ceiling = last_id


async def _handle_message(message, info: ChatInfo, archive: Archive, policy: MediaPolicy, stats: dict) -> None:
    record = await _record_from_message(message, info)
    if record is None:
        return
    result = archive.prepare(info, record, policy, render=False)
    if result.new:
        stats["new"] += 1
    for item in result.downloads:
        await _download(message, info, archive, record, item, stats)


async def _download(message, info, archive, record, item, stats) -> None:
    destination = archive.path_in_chat(info.id, item["path"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    progress = _progress(destination.name, item.get("size") or 0)
    try:
        result = await message.download_media(file=str(partial), progress_callback=progress)
        if not result:
            raise RuntimeError("Telegram returned no file")
        result_path = Path(result)
        if result_path.resolve() != partial.resolve() and result_path.exists():
            result_path.replace(partial)
        partial.replace(destination)
    except FloodWaitError:
        partial.unlink(missing_ok=True)
        raise
    except Exception as exc:
        partial.unlink(missing_ok=True)
        remove_empty_parents(destination.parent, archive._folders[info.id])
        archive.mark_media(info.id, record, item, status="failed", reason=str(exc), path=None, render=False)
        say(f"  download failed #{record.get('message_id')} {item.get('file_name')}: {exc}")
        return
    archive.mark_media(
        info.id,
        record,
        item,
        status="saved",
        path=item["path"],
        size=destination.stat().st_size,
        render=False,
    )
    if item.get("role") == "image":
        stats["images"] += 1
    else:
        stats["files"] += 1


def _progress(name: str, size: int):
    last = {"pct": -10}

    def callback(received: int, total: int | None) -> None:
        total = total or size
        if not total or total < 1_000_000:
            return
        pct = int(received * 100 / total)
        if pct >= last["pct"] + 10:
            last["pct"] = pct
            say(f"  {name} {pct}%")

    return callback


async def _record_from_message(message, info: ChatInfo) -> dict | None:
    action = getattr(message, "action", None)
    parts: list[str] = []
    body = getattr(message, "message", None) or ""
    if body:
        parts.append(body)
    action_line = describe_action(action)
    if action_line:
        parts.append(action_line)
    media_obj = getattr(message, "media", None)
    extra = extra_text(media_obj)
    if extra:
        parts.append(extra)
    media = [] if action is not None else describe_media(media_obj)
    text = "\n".join(parts).strip()
    if not text and not media:
        return None
    sender = message.sender
    if sender is None and getattr(message, "sender_id", None):
        try:
            sender = await message.get_sender()
        except Exception:
            sender = None
    sender_id, sender_name, username = _sender_fields(sender, getattr(message, "sender_id", None))
    reply_id, thread_id = _reply_and_thread(message)
    moment = getattr(message, "date", None) or datetime.now(timezone.utc)
    edited = getattr(message, "edit_date", None)
    grouped = getattr(message, "grouped_id", None)
    return {
        "kind": "message",
        "source": "history",
        "chat_id": info.id,
        "message_id": message.id,
        "date": _local_iso(moment),
        "edited_at": _local_iso(edited) if edited else None,
        "sender_id": sender_id,
        "sender_name": sender_name,
        "sender_username": username,
        "text": text,
        "media": media,
        "media_group_id": str(grouped) if grouped else None,
        "reply_to_message_id": reply_id,
        "thread_id": thread_id,
        "forwarded_from": _forward_label(message),
        "raw": {
            "id": message.id,
            "grouped_id": grouped,
            "action": type(action).__name__ if action else None,
        },
    }


async def _collect_targets(client, chat_specs: list[str], all_groups: bool):
    targets = []
    seen: set[int] = set()

    def add(entity) -> None:
        peer_id = utils.get_peer_id(entity)
        if peer_id not in seen:
            seen.add(peer_id)
            targets.append(entity)

    if all_groups:
        async for dialog in client.iter_dialogs(ignore_migrated=True):
            if dialog.is_group:
                add(dialog.entity)
    for spec in chat_specs:
        add(await _resolve_chat(client, spec))
    if not targets:
        raise SystemExit("No matching chats. Run `tg-archive chats` and pass one of those ids.")
    return targets


async def _resolve_chat(client, spec: str):
    spec = spec.strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if spec.startswith(prefix):
            spec = spec[len(prefix) :].split("/")[0]
            break
    if spec.startswith("+") or spec.startswith("joinchat"):
        raise SystemExit("Pass a chat id from `tg-archive chats`, or an @username. Invite links are not chat ids.")
    if spec.lstrip("-").isdigit():
        try:
            return await client.get_entity(int(spec))
        except RPCError as exc:
            raise SystemExit(f"Could not open chat {spec}: {exc}") from exc
    try:
        return await client.get_entity(spec)
    except (RPCError, ValueError, TypeError):
        matches = []
        async for dialog in client.iter_dialogs(ignore_migrated=True):
            if dialog.name == spec or dialog.name.casefold() == spec.casefold():
                matches.append(dialog)
        if len(matches) == 1:
            return matches[0].entity
        if not matches:
            raise SystemExit(f"No chat named {spec!r}. Run: tg-archive chats") from None
        lines = "\n".join(f"  {dialog.id}  {dialog.name}" for dialog in matches)
        raise SystemExit(f"More than one chat matches {spec!r}:\n{lines}\nPass the numeric id.")


def _checkpoint(archive: Archive, chat_id: int, state: dict) -> None:
    archive.flush(chat_id)
    archive.set_history_state(
        chat_id,
        cursor=state.get("cursor"),
        newest_seen=state.get("newest_seen") or None,
        complete=True if state.get("complete") else None,
    )


def _chat_info(entity) -> ChatInfo:
    peer_id = utils.get_peer_id(entity)
    title = utils.get_display_name(entity) or str(peer_id)
    return ChatInfo(
        id=peer_id,
        title=title,
        type=_kind_of_entity(entity),
        username=getattr(entity, "username", None),
    )


def _kind_of_entity(entity) -> str:
    if isinstance(entity, types.User):
        return "private"
    if isinstance(entity, types.Channel) and getattr(entity, "megagroup", False):
        return "supergroup"
    if isinstance(entity, types.Channel):
        return "channel"
    if isinstance(entity, types.Chat):
        return "group"
    return "chat"


def _kind_of_dialog(dialog) -> str:
    if dialog.is_user:
        return "private"
    if dialog.is_group and dialog.is_channel:
        return "supergroup"
    if dialog.is_group:
        return "group"
    if dialog.is_channel:
        return "channel"
    return "other"


def _sender_fields(sender, sender_id) -> tuple[int | None, str, str | None]:
    if sender is None:
        if sender_id:
            return sender_id, str(sender_id), None
        return None, "unknown", None
    username = getattr(sender, "username", None)
    title = getattr(sender, "title", None)
    if title:
        return getattr(sender, "id", sender_id), title, username
    first = getattr(sender, "first_name", None) or ""
    last = getattr(sender, "last_name", None) or ""
    name = f"{first} {last}".strip() or username or (str(sender_id) if sender_id else "unknown")
    return getattr(sender, "id", sender_id), name, username


def _reply_and_thread(message) -> tuple[int | None, int | None]:
    reply = getattr(message, "reply_to", None)
    if reply is None:
        return None, None
    reply_id = getattr(reply, "reply_to_msg_id", None)
    thread_id = getattr(reply, "reply_to_top_id", None)
    if getattr(reply, "forum_topic", False) and not thread_id:
        thread_id = reply_id
    return reply_id, thread_id


def _forward_label(message) -> str | None:
    forwarded = getattr(message, "fwd_from", None)
    if forwarded is None:
        return None
    if getattr(forwarded, "from_name", None):
        return forwarded.from_name
    if getattr(forwarded, "post_author", None):
        return forwarded.post_author
    from_id = getattr(forwarded, "from_id", None)
    if from_id is None:
        return None
    try:
        return str(utils.get_peer_id(from_id))
    except (TypeError, ValueError):
        return None


def _local_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().isoformat(timespec="seconds")


async def _show_me(client) -> None:
    me = await client.get_me()
    say(f"Signed in as {utils.get_display_name(me)} (id {me.id}).")
