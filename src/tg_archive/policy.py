from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MediaPolicy:
    """Text is always stored. Images and other files are optional."""

    images: bool
    files: bool

    def allow(self, role: str) -> bool:
        if role == "image":
            return self.images
        if role == "file":
            return self.files
        return False

    def skip_reason(self, role: str) -> str:
        if role == "image":
            return "images disabled"
        if role == "file":
            return "files disabled"
        return "unsupported"

    def label(self) -> str:
        parts = ["text"]
        if self.images:
            parts.append("images")
        if self.files:
            parts.append("files")
        return ", ".join(parts)


def role_for(kind: str, mime: str | None) -> str:
    """`image` is photos, GIFs, and picture files. Everything else is a file."""
    if kind in {"photo", "animation", "gif"}:
        return "image"
    if kind == "sticker":
        return "file"
    if mime and mime.startswith("image/"):
        return "image"
    return "file"
