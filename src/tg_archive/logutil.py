from __future__ import annotations

from datetime import datetime
from pathlib import Path


def say(message: str) -> None:
    print(message, flush=True)


def redact(text: str, secret: str) -> str:
    if secret and secret in text:
        return text.replace(secret, "<token>")
    return text


def log_error(data_dir: Path, message: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with (data_dir / "errors.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp}  {message}\n")
