"""Sidecar persistence (§8).

The sidecar lives in the **output** directory (``<out>/<book_slug>/.pdfsplit.json``),
never beside the source. It restores level / mode / band and per-entry overrides. If
the source file's size or mtime no longer match what was recorded, the overrides are
**ignored with a warning** — page numbers from a different revision are dangerous —
while level / mode / band are still restored.
"""

from __future__ import annotations

import json
import os

from .engine import override_key, slugify  # noqa: F401  (re-exported for callers)
from .model import Band

SIDECAR_NAME = ".pdfsplit.json"
VERSION = 1


def sidecar_path(out_dir: str, book_slug: str) -> str:
    return os.path.join(out_dir, book_slug, SIDECAR_NAME)


def _empty_overrides() -> dict:
    return {"starts": {}, "include": {}}


def save(sidecar_file: str, *, source: str, level: int, notebooklm: bool,
         band: Band, overrides: dict | None = None) -> str:
    source = os.path.abspath(source)
    st = os.stat(source)
    data = {
        "version": VERSION,
        "source": source,
        "source_size": st.st_size,
        "source_mtime": st.st_mtime,
        "level": level,
        "notebooklm": notebooklm,
        "band": band.to_dict(),
        "overrides": overrides or _empty_overrides(),
    }
    os.makedirs(os.path.dirname(sidecar_file), exist_ok=True)
    with open(sidecar_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return sidecar_file


def load(sidecar_file: str, source: str) -> dict | None:
    """Return ``{"level","notebooklm","band","overrides","warnings"}`` or ``None`` if
    no sidecar exists. Overrides are dropped (with a warning) on a source mismatch."""
    if not os.path.exists(sidecar_file):
        return None

    with open(sidecar_file, encoding="utf-8") as f:
        data = json.load(f)

    warnings: list[str] = []
    band = Band.from_dict(data.get("band", {}))
    overrides = data.get("overrides") or _empty_overrides()
    overrides.setdefault("starts", {})
    overrides.setdefault("include", {})

    try:
        st = os.stat(source)
        same = (st.st_size == data.get("source_size")
                and abs(st.st_mtime - float(data.get("source_mtime", -1))) < 1e-6)
    except OSError:
        same = False

    if not same:
        warnings.append(
            "Source file no longer matches the sidecar (size/mtime differ); "
            "ignoring saved overrides. Level, mode and band are still restored."
        )
        overrides = _empty_overrides()

    return {
        "level": data.get("level"),
        "notebooklm": data.get("notebooklm"),
        "band": band,
        "overrides": overrides,
        "warnings": warnings,
    }
