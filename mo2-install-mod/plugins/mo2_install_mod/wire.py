"""Local request files shared by MO2 and the standalone client."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

VERSION = "1.1.0"
PROTOCOL = 1
MAX_REQUEST = 64 * 1024
TERMINAL = {"complete", "failed", "cancelled"}


class Error(Exception):
    def __init__(self, code: str, message: str, **context):
        super().__init__(message)
        self.code = code
        self.context = context

    def json(self) -> dict:
        return {"code": self.code, "message": str(self), **self.context}


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise Error("INVALID_ID", "Expected a 32-character operation or session ID.")
    return value


def path_key(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def channel(instance: str | Path) -> Path:
    digest = hashlib.sha256(path_key(instance).encode()).hexdigest()[:24]
    return Path(tempfile.gettempdir()) / "mo2-install-mod" / digest


def read_json(path: Path, limit: int = MAX_REQUEST) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise Error("REQUEST_TOO_LARGE", f"JSON exceeds {limit} bytes.")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise Error("INVALID_REQUEST", "Expected a JSON object.")
    return value


def write_json(path: Path, value: dict) -> None:
    """Publish a complete file; request and response names are unique."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_name(name: str) -> str:
    if (not isinstance(name, str) or not name or name != name.strip()
            or name.endswith(".") or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name in {".", ".."}
            or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name)):
        raise Error("INVALID_NAME", "Use an exact Windows directory name without surrounding whitespace.")
    return name
