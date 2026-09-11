"""Create an MO2 download sidecar from Nexus upload information."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import re
import sys

FILE_CATEGORIES = {"MAIN": 1, "UPDATE": 2, "OPTIONAL": 3, "OLD_VERSION": 4,
                   "MISCELLANEOUS": 5, "DELETED": 6, "ARCHIVED": 7}
GAME_NAMES = {"skyrimspecialedition": "SkyrimSE"}


def positive(value) -> int:
    if not isinstance(value, (str, int)) or isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)) or int(value) <= 0:
        raise ValueError("Expected a positive decimal ID or byte count.")
    return int(value)


def encode(value: str | int | bool) -> str:
    """Qt INI text encoding, without importing Qt into the external interpreter."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value):
        raise ValueError("INI text contains an unsupported control character.")
    if value.startswith("@"):
        value = "@" + value
    quote = value != value.strip() or any(c in value for c in '\\"\n\r\t,;')
    value = (value.replace("\\", "\\\\").replace('"', '\\"')
             .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
    return '"' + value + '"' if quote else value


def fields(*, game: str, mod_id: int, file_id: int, file_name: str, file_version: str,
           file_category: str, mod_name: str, mod_category: int, description: str = "") -> dict:
    if game not in GAME_NAMES:
        raise ValueError("Use the skyrimspecialedition Nexus catalog.")
    mod_id, file_id, mod_category = map(positive, (mod_id, file_id, mod_category))
    if max(mod_id, file_id, mod_category) > 0x7FFFFFFF:
        raise ValueError("Nexus IDs must fit MO2's signed 32-bit fields.")
    if file_category not in FILE_CATEGORIES:
        raise ValueError("Unknown Nexus file category.")
    for label, value in (("file name", file_name), ("file version", file_version), ("mod name", mod_name)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Provide the selected upload's {label}.")
    if not isinstance(description, str):
        raise ValueError("Provide the upload description as plain text.")
    description = description.replace("\r\n", "\n").replace("\r", "\n")
    return {"gameName": GAME_NAMES[game], "modID": mod_id, "fileID": file_id,
            "repository": "Nexus", "name": file_name, "version": file_version,
            "fileCategory": FILE_CATEGORIES[file_category], "modName": mod_name,
            "category": mod_category,
            "description": html.escape(description).replace("\n", "<br />\n"),
            "installed": False, "uninstalled": False}


def create(archive: Path, *, expected_size: int | None = None, **metadata) -> Path:
    archive = archive.resolve()
    if not archive.is_file() or archive.suffix.lower() not in {".zip", ".7z", ".rar"} or archive.stat().st_size == 0:
        raise ValueError("Use a nonempty ZIP, 7z, or RAR file.")
    if expected_size is not None and archive.stat().st_size != positive(expected_size):
        raise ValueError("The archive's byte count differs from expected_size.")
    values = fields(**metadata)
    content = "[General]\n" + "".join(f"{key}={encode(value)}\n" for key, value in values.items())
    sidecar = Path(str(archive) + ".meta")
    # A pre-existing sidecar may contain MO2's installation state and cached data.
    with sidecar.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    return sidecar


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="completed archive")
    parser.add_argument("--game", choices=GAME_NAMES, required=True, help="Nexus game catalog, not the managed game")
    parser.add_argument("--mod-id", type=positive, required=True)
    parser.add_argument("--file-id", type=positive, required=True)
    parser.add_argument("--file-name", required=True, help="selected upload's display name")
    parser.add_argument("--file-version", required=True, help="selected upload's version")
    parser.add_argument("--file-category", choices=FILE_CATEGORIES, required=True)
    parser.add_argument("--mod-name", required=True, help="Nexus mod title as plain text")
    parser.add_argument("--mod-category", type=positive, required=True, help="Nexus mod category ID, not a local MO2 category")
    parser.add_argument("--description-file", type=Path, help="UTF-8 plain-text description of the selected upload")
    parser.add_argument("--expected-size", type=positive, help="expected archive size in bytes, when known")
    args = vars(parser.parse_args(argv))
    try:
        archive = args.pop("archive")
        description_file = args.pop("description_file")
        if description_file is not None:
            args["description"] = description_file.read_text(encoding="utf-8-sig")
        sidecar = create(archive, **args)
        print(json.dumps({"status": "complete", "sidecar": str(sidecar)}))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
