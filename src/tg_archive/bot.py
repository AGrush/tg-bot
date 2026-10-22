from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import httpx

from tg_archive.bot_events import normalize_update
from tg_archive.config import Settings
from tg_archive.logutil import log_error, redact, say
from tg_archive.policy import MediaPolicy
from tg_archive.store import Archive, load_offset, remove_empty_parents, save_offset

HOSTED_LIMIT = 20 * 1024 * 1024
TOO_BIG = "larger than 20 MB on the hosted bot API"
_HOSTED = {"https://api.telegram.org", "http://api.telegram.org"}


class TelegramError(RuntimeError):
    def __init__(self, description: str, retry_after: int | None = None):
        super().__init__(description)
        self.retry_after = retry_after


def hosted_too_big(size: int | None, api_base: str) -> bool:
    if size is None or size <= HOSTED_LIMIT:
        return False
    return api_base.rstrip("/") in _HOSTED


async def run_bot(settings: Settings, policy: MediaPolicy) -> None:
    settings.require_bot()
    archive = Archive(settings.data_dir)
    offset = load_offset(archive.root)
    failures: dict[int, int] = {}
    ignored: set[int] = set()
    announced: set[int] = set()
    hinted = {"big": False}
    timeout = httpx.Timeout(10.0, read=45.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        try:
            await _call(http, settings, "deleteWebhook", drop_pending_updates=False)
        except TelegramError as exc:
            say(f"Could not clear an old webhook: {exc}")
        try:
            me = await _call(http, settings, "getMe")
        except TelegramError as exc:
            raise SystemExit(
                f"Telegram rejected BOT_TOKEN ({exc}). Paste only the token from @BotFather."
            ) from exc
        username = me.get("username") or me.get("first_name") or "bot"
        say(f"Listening as @{username}.")
        say(f"Saving into {settings.data_dir.resolve()}")
        say(f"Live media: {policy.label()}.")
        say("Add this bot to a group after privacy is turned off. Press Ctrl+C to stop.")
        await retry_downloads(http, settings, archive, policy, include_failed=False, announce_empty=False, hinted=hinted)
        try:
            while True:
                try:
                    updates = await _call(
                        http,
                        settings,
                        "getUpdates",
                        offset=offset,
                        timeout=30,
                        limit=100,
                        allowed_updates=[
                            "message",
                            "edited_message",
                            "channel_post",
                            "edited_channel_post",
                            "my_chat_member",
                        ],
                    )
                except TelegramError as exc:
                    await _pause(exc, fallback=5)
                    continue
                except httpx.HTTPError as exc:
                    say(f"Connection problem: {redact(str(exc), settings.bot_token)}. Retrying in 5s.")
                    await _sleep(5)
                    continue
                for update in updates:
                    update_id = int(update["update_id"])
                    try:
                        await _handle_update(http, settings, archive, policy, update, ignored, announced, hinted)
                    except Exception as exc:
                        failures[update_id] = failures.get(update_id, 0) + 1
                        message = redact(str(exc), settings.bot_token)
                        log_error(settings.data_dir, f"update {update_id}: {message}")
                        say(f"Could not save update {update_id}: {message}")
                        if failures[update_id] < 3:
                            await _sleep(2)
                            break
                        say(f"Skipping update {update_id} after 3 failures.")
                    offset = update_id + 1
                    save_offset(archive.root, offset)
                    failures.pop(update_id, None)
                archive.sync()
        finally:
            archive.flush()
            say("Stopped. Messages already saved stay in data/chats/.")


async def run_retry(settings: Settings, policy: MediaPolicy) -> None:
    settings.require_bot()
    archive = Archive(settings.data_dir)
    timeout = httpx.Timeout(10.0, read=120.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        await retry_downloads(
            http,
            settings,
            archive,
            policy,
            include_failed=True,
            announce_empty=True,
            hinted={"big": False},
        )
    say("Retry finished.")


async def retry_downloads(
    http: httpx.AsyncClient,
    settings: Settings,
    archive: Archive,
    policy: MediaPolicy,
    *,
    include_failed: bool,
    announce_empty: bool,
    hinted: dict,
) -> None:
    jobs, missing = archive.incomplete_media(policy, include_failed=include_failed)
    if not jobs and announce_empty:
        say("Nothing to retry.")
    elif jobs:
        say(f"Retrying {len(jobs)} unfinished download{'s' if len(jobs) != 1 else ''}.")
    for job in jobs:
        chat = job["chat"]
        record = job["record"]
        item = job["item"]
        folder = archive.chat_folder(chat)
        if not item.get("path"):
            item["path"] = archive.allocate_media_path(folder, record, item)
        await _download(http, settings, archive, chat, record, item, hinted)
        archive.flush(chat.id)
    if missing and announce_empty:
        say(
            f"{missing} file{'s' if missing != 1 else ''} came from history mode and have no bot file id. "
            "Run tg-archive history on that chat with --images and/or --files."
        )


async def _handle_update(http, settings, archive, policy, update, ignored, announced, hinted) -> None:
    for chat, record in normalize_update(update):
        if not settings.chat_allowed(chat.id):
            if chat.id not in ignored:
                ignored.add(chat.id)
                say(f"Ignoring {chat.title} ({chat.id}): not in ALLOWED_CHAT_IDS.")
            continue
        result = archive.prepare(chat, record, policy, render=False)
        try:
            for item in result.downloads:
                await _download(http, settings, archive, chat, record, item, hinted)
        finally:
            archive.flush(chat.id)
        if chat.id not in announced:
            announced.add(chat.id)
            say(f"Archiving {chat.title} ({chat.id})")
            say(f"  {result.folder}")
        if result.new and record.get("kind") == "membership":
            say(f"{chat.title}: {record.get('text')}")
        elif result.new and not result.downloads:
            skipped = sorted({item.get("role") for item in record.get("media") or [] if item.get("status") == "skipped"})
            extra = f" ({', '.join(skipped)} not downloaded)" if skipped else ""
            label = "edited" if record.get("kind") == "edit" else "saved"
            target = f"#{record.get('message_id')}" if record.get("message_id") is not None else record.get("text", "")
            say(f"{label}  {chat.title}  {target}{extra}")


async def _download(http, settings, archive, chat, record, item, hinted: dict) -> None:
    message_id = record.get("message_id")
    name = item.get("file_name") or "file"
    if hosted_too_big(item.get("size"), settings.bot_api_base):
        archive.mark_media(chat.id, record, item, status="failed", reason=TOO_BIG, path=None, render=False)
        say(f"not downloaded  {chat.title}  #{message_id}  {name}: {TOO_BIG}")
        if not hinted["big"]:
            hinted["big"] = True
            say("  Files over 20 MB need history mode, or a local Bot API server in BOT_API_BASE.")
        return
    try:
        info = await _call(http, settings, "getFile", file_id=item.get("file_id"))
    except TelegramError as exc:
        if exc.retry_after:
            await _pause(exc, fallback=exc.retry_after)
            try:
                info = await _call(http, settings, "getFile", file_id=item.get("file_id"))
            except TelegramError as again:
                _fail_download(archive, settings, chat, record, item, str(again))
                return
        else:
            _fail_download(archive, settings, chat, record, item, str(exc))
            return
    file_path = info.get("file_path") or ""
    if hosted_too_big(info.get("file_size"), settings.bot_api_base):
        archive.mark_media(chat.id, record, item, status="failed", reason=TOO_BIG, path=None, render=False)
        say(f"not downloaded  {chat.title}  #{message_id}  {name}: {TOO_BIG}")
        return
    if not item.get("path"):
        item["path"] = archive.allocate_media_path(archive._folders[chat.id], record, item)
    destination = archive.path_in_chat(chat.id, item["path"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    try:
        if Path(file_path).is_absolute():
            shutil.copyfile(file_path, partial)
        else:
            url = f"{settings.bot_api_base}/file/bot{settings.bot_token}/{file_path}"
            async with http.stream("GET", url, timeout=httpx.Timeout(30.0, read=120.0)) as response:
                response.raise_for_status()
                with partial.open("wb") as handle:
                    async for chunk in response.aiter_bytes(256 * 1024):
                        handle.write(chunk)
        partial.replace(destination)
    except Exception as exc:
        partial.unlink(missing_ok=True)
        remove_empty_parents(destination.parent, archive._folders[chat.id])
        _fail_download(archive, settings, chat, record, item, redact(str(exc), settings.bot_token))
        return
    archive.mark_media(
        chat.id,
        record,
        item,
        status="saved",
        path=item["path"],
        size=destination.stat().st_size,
        render=False,
    )
    say(f"saved  {chat.title}  #{message_id}  {destination.name}")


def _fail_download(archive, settings, chat, record, item, reason: str) -> None:
    reason = redact(reason, settings.bot_token)
    archive.mark_media(chat.id, record, item, status="failed", reason=reason, path=None, render=False)
    say(f"download failed  {chat.title}  #{record.get('message_id')}  {item.get('file_name')}: {reason}")
    say("  tg-archive retry will try this file again.")


async def _call(http: httpx.AsyncClient, settings: Settings, method: str, **params):
    url = f"{settings.bot_api_base}/bot{settings.bot_token}/{method}"
    payload = {key: value for key, value in params.items() if value is not None}
    response = await http.post(url, json=payload)
    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise TelegramError(redact(response.text[:300], settings.bot_token)) from exc
    if not data.get("ok"):
        retry = (data.get("parameters") or {}).get("retry_after")
        description = redact(str(data.get("description") or response.text), settings.bot_token)
        raise TelegramError(description, int(retry) if retry else None)
    return data["result"]


async def _pause(exc: TelegramError, fallback: int) -> None:
    seconds = exc.retry_after or fallback
    say(f"Telegram asked for a {seconds}s pause." if exc.retry_after else f"{exc} Retrying in {seconds}s.")
    await _sleep(seconds)


async def _sleep(seconds: int) -> None:
    await asyncio.sleep(seconds)
