from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def find_env_files(start: Path | None = None) -> list[Path]:
    cwd = (start or Path.cwd()).resolve()
    files: list[Path] = []
    cwd_env = cwd / ".env"
    if cwd_env.is_file():
        files.append(cwd_env)
    for parent in [cwd, *cwd.parents]:
        if (parent / "pyproject.toml").is_file():
            env = parent / ".env"
            if env.is_file() and env not in files:
                files.append(env)
            break
    return files


def load_dotenv_files(start: Path | None = None) -> None:
    for path in find_env_files(start):
        load_dotenv(path)


def load_dotenv(path: Path) -> None:
    """Fill empty environment keys from a KEY=value file. Existing values stay."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"Expected true or false, got {value!r}.")


def parse_allowed(value: str | None) -> set[int] | None:
    if value is None or not value.strip():
        return None
    ids: set[int] = set()
    for piece in value.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            ids.add(int(piece))
        except ValueError as exc:
            raise SystemExit(
                f"ALLOWED_CHAT_IDS must be numeric chat ids separated by commas. Got {piece!r}."
            ) from exc
    return ids or None


@dataclass
class Settings:
    bot_token: str
    api_id: int | None
    api_hash: str
    phone: str
    data_dir: Path
    allowed_chat_ids: set[int] | None
    download_images: bool
    download_files: bool
    bot_api_base: str

    @property
    def session_path(self) -> Path:
        return self.data_dir / "user"

    def require_bot(self) -> None:
        if not self.bot_token:
            raise SystemExit(
                "BOT_TOKEN is empty. Copy .env.example to .env and paste the token from @BotFather."
            )

    def require_user(self) -> None:
        if not self.api_id or not self.api_hash:
            raise SystemExit(
                "API_ID and API_HASH are empty. Create them at https://my.telegram.org "
                "and put them in .env. History download uses them. The live bot does not."
            )

    def chat_allowed(self, chat_id: int) -> bool:
        return self.allowed_chat_ids is None or chat_id in self.allowed_chat_ids


def settings_from_env() -> Settings:
    api_raw = os.environ.get("API_ID", "").strip()
    api_id: int | None
    if not api_raw:
        api_id = None
    else:
        try:
            api_id = int(api_raw)
        except ValueError as exc:
            raise SystemExit("API_ID must be the number from my.telegram.org.") from exc
    data_dir = Path(os.environ.get("DATA_DIR", "data")).expanduser()
    base = os.environ.get("BOT_API_BASE", "https://api.telegram.org").strip().rstrip("/")
    return Settings(
        bot_token=os.environ.get("BOT_TOKEN", "").strip(),
        api_id=api_id,
        api_hash=os.environ.get("API_HASH", "").strip(),
        phone=os.environ.get("TELEGRAM_PHONE", "").strip(),
        data_dir=data_dir,
        allowed_chat_ids=parse_allowed(os.environ.get("ALLOWED_CHAT_IDS")),
        download_images=parse_bool(os.environ.get("DOWNLOAD_IMAGES"), default=True),
        download_files=parse_bool(os.environ.get("DOWNLOAD_FILES"), default=True),
        bot_api_base=base or "https://api.telegram.org",
    )
