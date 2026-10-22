from __future__ import annotations

import re

_UNSAFE_FILENAME = re.compile(r"[\x00-\x1f<>:\"|?*]")
_UNSAFE_SLUG = re.compile(r"[\x00-\x1f\\/:*?\"<>|]+")
_WHITESPACE = re.compile(r"\s+")


def safe_filename(name: str) -> str:
    """Return a single path segment. Directory pieces in `name` are dropped."""
    cleaned = (name or "").replace("\\", "/")
    cleaned = cleaned.split("/")[-1]
    cleaned = _UNSAFE_FILENAME.sub("_", cleaned).strip(" .")
    if cleaned in {"", ".", ".."}:
        return "file"
    if len(cleaned) > 120:
        stem, dot, ext = cleaned.rpartition(".")
        if dot and stem and len(ext) <= 20:
            keep = 120 - len(ext) - 1
            cleaned = f"{stem[:keep]}.{ext}"
        else:
            cleaned = cleaned[:120]
    return cleaned or "file"


def slugify(title: str) -> str:
    """A readable folder suffix. Letters from any language are kept."""
    text = _UNSAFE_SLUG.sub(" ", (title or "").strip())
    text = _WHITESPACE.sub("-", text).strip(" .-")
    if not text:
        return "chat"
    return text[:80]


def chat_dirname(chat_id: int, title: str) -> str:
    return f"{chat_id}__{slugify(title)}"
