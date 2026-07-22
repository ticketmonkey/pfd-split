"""pdfsplit — split DRM-free technical ebook PDFs into chapter-sized PDFs.

Stage 1 provides the pure planning engine (no CLI, no web, no PDF emission). The
CLI and web skins are added in later stages as thin wrappers over this package.
"""

from __future__ import annotations

from . import engine, errors, model, sidecar, verify
from .engine import outline_summary, plan_book, slugify
from .errors import LevelNotFoundError, NoOutlineError
from .model import Band, BookPlan, Chunk, OutlineEntry, VerifyResult

__all__ = [
    "engine",
    "errors",
    "model",
    "sidecar",
    "verify",
    "plan_book",
    "outline_summary",
    "slugify",
    "Band",
    "BookPlan",
    "Chunk",
    "OutlineEntry",
    "VerifyResult",
    "NoOutlineError",
    "LevelNotFoundError",
]
