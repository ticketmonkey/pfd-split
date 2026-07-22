"""Localhost boundary-review UI (§10).

``web.py`` is a thin skin over the pure engine (§5), emitter (§7) and sidecar (§8) —
it contains **no planning logic of its own** (invariant §3.6). All boundaries come from
``engine.plan_book``; the server only resolves book IDs, renders thumbnails, and reads
and writes the sidecar.

Security posture (§10):

- ``serve`` binds **127.0.0.1 only** (in ``cli.py``); these are copyrighted books you own,
  nothing leaves the machine and nothing is uploaded.
- URLs never carry a filesystem path. A book is addressed by
  ``sha1(abspath).hexdigest()[:12]`` resolved through a dict built by the library scan;
  an unknown or forged ID (including a path-like ``..%2F..%2Fetc%2Fpasswd``) is simply not a
  key, so it 404s. That dict is the path-traversal guard.
- Thumbnails render server-side (``page.get_pixmap``) — there is no JavaScript PDF library in
  this project at all.
"""

from __future__ import annotations

import hashlib
import os
from contextlib import asynccontextmanager
from functools import lru_cache

import fitz
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import engine, sidecar, write
from .errors import LevelNotFoundError, NoOutlineError
from .model import Band

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _book_id(abspath: str) -> str:
    return hashlib.sha1(abspath.encode("utf-8")).hexdigest()[:12]


def _scan_library(library_dir: str) -> dict[str, str]:
    """Map ``book_id -> absolute path`` for every ``*.pdf`` directly under ``library_dir``.

    Non-recursive and sorted, so the left-rail order is stable. The returned dict *is* the
    path-traversal guard: only IDs it contains resolve to a file.
    """
    books: dict[str, str] = {}
    if not os.path.isdir(library_dir):
        return books
    for name in sorted(os.listdir(library_dir)):
        if not name.lower().endswith(".pdf"):
            continue
        abspath = os.path.abspath(os.path.join(library_dir, name))
        if os.path.isfile(abspath):
            books[_book_id(abspath)] = abspath
    return books


def create_app(library_dir: str, out_dir: str = "./out") -> FastAPI:
    """Build the review-UI app over ``library_dir`` (read-only) and ``out_dir`` (all writes).

    A factory rather than a module-level app so the CLI and the tests can each point it at a
    different library without global state.
    """
    library_dir = os.path.abspath(library_dir)
    out_dir = os.path.abspath(out_dir)

    # Open ``fitz.Document`` handles are cached rather than reopened per thumbnail request;
    # ``_meta`` caches the cheap per-book facts the left rail needs. Both are closed/cleared
    # on shutdown.
    _docs: dict[str, fitz.Document] = {}
    _meta: dict[str, dict] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            for doc in _docs.values():
                try:
                    doc.close()
                except Exception:
                    pass
            _docs.clear()

    app = FastAPI(title="pdfsplit", lifespan=lifespan)

    if os.path.isdir(_STATIC_DIR):
        app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _resolve(book_id: str) -> str:
        """book_id -> path, or 404. Rejects unknown/forged IDs (path-traversal guard)."""
        path = _scan_library(library_dir).get(book_id)
        if path is None:
            raise HTTPException(status_code=404, detail="unknown book id")
        return path

    def _doc(book_id: str, path: str) -> fitz.Document:
        doc = _docs.get(book_id)
        if doc is None:
            doc = fitz.open(path)          # read-only; never saved, never mutated
            _docs[book_id] = doc
        return doc

    def _book_facts(book_id: str, path: str) -> dict:
        facts = _meta.get(book_id)
        if facts is None:
            doc = _doc(book_id, path)
            try:
                engine.read_outline(doc, [])
                has_outline = True
            except NoOutlineError:
                has_outline = False
            title, _author = engine.derive_title(doc, path)
            facts = {
                "title": title,
                "pages": doc.page_count,
                "has_outline": has_outline,
            }
            _meta[book_id] = facts
        return facts

    @lru_cache(maxsize=200)
    def _thumb(book_id: str, n: int, w: int) -> bytes:
        """In-memory LRU (~200) of rendered PNGs, keyed by ``(id, n, w)`` (§10.1)."""
        path = _scan_library(library_dir).get(book_id)
        if path is None:
            raise HTTPException(status_code=404, detail="unknown book id")
        doc = _doc(book_id, path)
        if not (0 <= n < doc.page_count):
            raise HTTPException(status_code=404, detail="page out of range")
        from . import render
        return render.thumbnail_png(doc[n], w)

    def _sidecar_file(path: str) -> str:
        return sidecar.sidecar_path(out_dir, engine.slugify(_book_slug_title(path)))

    def _book_slug_title(path: str) -> str:
        doc = fitz.open(path)
        try:
            title, _ = engine.derive_title(doc, path)
        finally:
            doc.close()
        return title

    def _load_overrides(path: str):
        saved = sidecar.load(_sidecar_file(path), path)
        return saved["overrides"] if saved else None, saved

    def _plan_json(path: str, *, level, notebooklm, band) -> dict:
        """Build a book plan and serialize it, augmented with the two pieces of data the
        frontend needs that ``BookPlan.to_dict`` does not carry: the available outline levels
        (for the toolbar) and each chunk's stable override key (for POSTing corrections).

        Neither augmentation touches boundaries — the API does not drift from the engine."""
        overrides, _saved = _load_overrides(path)
        plan = engine.plan_book(
            path, level=level, notebooklm=notebooklm, band=band, overrides=overrides
        )
        data = plan.to_dict()

        # Available levels + per-chunk override key both come from the flat outline.
        doc = _doc(_book_id(os.path.abspath(path)), os.path.abspath(path))
        entries = engine.read_outline(doc, [])
        data["available_levels"] = sorted({e.level for e in entries})
        for cd, chunk in zip(data["chunks"], plan.chunks):
            cd["override_key"] = (
                engine.override_key(entries[chunk.entries[0]]) if chunk.entries else None
            )
        # The applied overrides, so the frontend seeds its authoritative set and posts back
        # the full merged state rather than clobbering earlier corrections.
        data["overrides"] = overrides or {"starts": {}, "include": {}}
        return data

    # ------------------------------------------------------------------ #
    # Routes
    # ------------------------------------------------------------------ #

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(os.path.join(_STATIC_DIR, "index.html"))

    @app.get("/api/library")
    def library() -> dict:
        books = []
        for book_id, path in _scan_library(library_dir).items():
            facts = _book_facts(book_id, path)
            books.append({
                "id": book_id,
                "filename": os.path.basename(path),
                "title": facts["title"],
                "pages": facts["pages"],
                "has_outline": facts["has_outline"],
            })
        return {"dir": library_dir, "books": books}

    @app.get("/api/books/{book_id}/plan")
    def plan(book_id: str, level: int = 1, notebooklm: bool = False,
             floor: int = 6000, target: int = 12000, ceiling: int = 20000):
        path = _resolve(book_id)
        band = Band(floor=floor, target=target, ceiling=ceiling)
        try:
            return _plan_json(path, level=level, notebooklm=notebooklm, band=band)
        except (NoOutlineError, LevelNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/books/{book_id}/page/{n}.png")
    def page_png(book_id: str, n: int, w: int = 280) -> Response:
        _resolve(book_id)                 # 404s unknown/forged IDs before rendering
        png = _thumb(book_id, n, w)
        return Response(content=png, media_type="image/png")

    @app.post("/api/books/{book_id}/overrides")
    async def overrides(book_id: str, request: Request) -> dict:
        path = _resolve(book_id)
        body = await request.json()
        new_overrides = {
            "starts": body.get("starts", {}) or {},
            "include": body.get("include", {}) or {},
        }
        # Preserve the last-used level / mode / band from the sidecar so the restored view
        # is unchanged; only the overrides are being updated here.
        _existing, saved = _load_overrides(path)
        level = saved["level"] if saved and saved.get("level") is not None else 1
        notebooklm = bool(saved["notebooklm"]) if saved else False
        band = saved["band"] if saved else Band()
        sidecar.save(
            _sidecar_file(path), source=path, level=level, notebooklm=notebooklm,
            band=band, overrides=new_overrides,
        )
        return {"ok": True}

    @app.post("/api/books/{book_id}/split")
    async def split(book_id: str, request: Request) -> dict:
        path = _resolve(book_id)
        body = await request.json()
        level = int(body.get("level", 1))
        notebooklm = bool(body.get("notebooklm", False))
        band = Band(
            floor=int(body.get("floor", 6000)),
            target=int(body.get("target", 12000)),
            ceiling=int(body.get("ceiling", 20000)),
        )
        prefix_book = bool(body.get("prefix_book", False))
        also_text = bool(body.get("also_text", False))
        header_page = bool(body.get("header_page", True))

        overrides, _saved = _load_overrides(path)
        try:
            book_plan = engine.plan_book(
                path, level=level, notebooklm=notebooklm, band=band, overrides=overrides
            )
        except (NoOutlineError, LevelNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        result = write.write_book(
            book_plan, out_dir, prefix_book=prefix_book, also_text=also_text,
            header_page=header_page,
        )
        # Persist the run's settings + overrides so reopening the book restores the view.
        sidecar.save(
            _sidecar_file(path), source=path, level=level, notebooklm=notebooklm,
            band=band, overrides=overrides,
        )
        return {
            "out_dir": result.out_dir,
            "written": result.written,
            "texts": result.texts,
            "index": result.index,
        }

    return app
