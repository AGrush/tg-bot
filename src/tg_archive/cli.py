from __future__ import annotations

import argparse
import asyncio

from tg_archive import __version__
from tg_archive.bot import run_bot, run_retry
from tg_archive.config import load_dotenv_files, settings_from_env
from tg_archive.history import list_chats, login, run_history
from tg_archive.lock import ArchiveLock
from tg_archive.logutil import say
from tg_archive.policy import MediaPolicy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tg-archive",
        description="Save Telegram group messages and files into one local folder per chat.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tg-archive run\n"
            "  tg-archive login\n"
            "  tg-archive chats\n"
            "  tg-archive history --chat -1001234567890\n"
            "  tg-archive history --chat -1001234567890 --images\n"
            "  tg-archive history --chat -1001234567890 --files\n"
            "  tg-archive history --chat -1001234567890 --images --files\n"
            "  tg-archive retry\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"tg-archive {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Save new messages as they arrive.")
    run.add_argument("--images", action=argparse.BooleanOptionalAction, default=None, help="Download images. Default comes from DOWNLOAD_IMAGES.")
    run.add_argument("--files", action=argparse.BooleanOptionalAction, default=None, help="Download other files. Default comes from DOWNLOAD_FILES.")

    commands.add_parser("login", help="Sign in to your Telegram account for history download.")

    chats = commands.add_parser("chats", help="List groups and channels you can backfill.")
    chats.add_argument("--dms", action="store_true", help="Also list private chats.")

    history = commands.add_parser(
        "history",
        help="Download older messages. Text only, unless you add --images and/or --files.",
    )
    history.add_argument("--chat", action="append", default=None, help="Chat id, @username, t.me link, or exact title. Repeatable.")
    history.add_argument("--all-groups", action="store_true", help="Every group and supergroup on the signed-in account.")
    history.add_argument("--images", action="store_true", help="Also download photos, GIFs, and picture files.")
    history.add_argument("--files", action="store_true", help="Also download documents, audio, video, voice messages, and stickers.")
    history.add_argument("--yes", action="store_true", help="Skip the confirmation for --all-groups.")

    retry = commands.add_parser("retry", help="Retry media the live bot has not saved yet.")
    retry.add_argument("--images", action=argparse.BooleanOptionalAction, default=None)
    retry.add_argument("--files", action=argparse.BooleanOptionalAction, default=None)
    return parser


def main(argv: list[str] | None = None) -> None:
    load_dotenv_files()
    args = build_parser().parse_args(argv)
    settings = settings_from_env()
    try:
        with ArchiveLock(settings.data_dir / "archive.lock"):
            asyncio.run(_dispatch(args, settings))
    except (KeyboardInterrupt, asyncio.CancelledError):
        say("\nStopped.")


async def _dispatch(args, settings) -> None:
    if args.command == "run":
        await run_bot(settings, _live_policy(settings, args))
    elif args.command == "login":
        await login(settings)
    elif args.command == "chats":
        await list_chats(settings, include_dms=args.dms)
    elif args.command == "history":
        await run_history(
            settings,
            list(args.chat or []),
            args.all_groups,
            MediaPolicy(images=bool(args.images), files=bool(args.files)),
            args.yes,
        )
    elif args.command == "retry":
        await run_retry(settings, _live_policy(settings, args))
    else:
        raise SystemExit(f"Unknown command {args.command}")


def _live_policy(settings, args) -> MediaPolicy:
    return MediaPolicy(
        images=settings.download_images if args.images is None else args.images,
        files=settings.download_files if args.files is None else args.files,
    )


if __name__ == "__main__":
    main()
