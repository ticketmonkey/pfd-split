"""Sidecar tests (§8)."""

from __future__ import annotations

import fitz

from pdfsplit import engine, sidecar
from pdfsplit.engine import override_key, plan_book
from pdfsplit.model import Band


def _out(tmp_path):
    return sidecar.sidecar_path(str(tmp_path / "out"), "the-book")


def test_sidecar_round_trips(simple_book, tmp_path):
    entries = engine.read_outline(fitz.open(simple_book), [])
    ch2 = [e for e in entries if e.title.startswith("Chapter 2")][0]
    key = override_key(ch2)
    scf = _out(tmp_path)

    sidecar.save(scf, source=simple_book, level=1, notebooklm=True,
                 band=Band(), overrides={"starts": {key: 16}, "include": {}})
    loaded = sidecar.load(scf, simple_book)

    assert loaded["level"] == 1
    assert loaded["notebooklm"] is True
    assert loaded["band"].floor == 6000
    assert loaded["overrides"]["starts"][key] == 16
    assert loaded["warnings"] == []


def test_override_key_is_independent_of_requested_level(part_book):
    entries = engine.read_outline(fitz.open(part_book), [])
    chapter = [e for e in entries if e.level == 2][0]
    key = override_key(chapter)
    # The key uses the entry's own level/page/title, so a --level change never shifts it.
    assert key == f"{chapter.level}:{chapter.page}:{engine.slugify(chapter.title)}"

    plan = plan_book(part_book, level=2,
                     overrides={"starts": {key: chapter.page}, "include": {}})
    target = [c for c in plan.chunks if chapter.idx in c.entries][0]
    assert target.verify.status == "ok"


def test_source_mismatch_ignores_overrides_but_restores_settings(simple_book, tmp_path):
    entries = engine.read_outline(fitz.open(simple_book), [])
    ch2 = [e for e in entries if e.title.startswith("Chapter 2")][0]
    key = override_key(ch2)
    scf = _out(tmp_path)

    sidecar.save(scf, source=simple_book, level=2, notebooklm=True,
                 band=Band(floor=5000), overrides={"starts": {key: 16}, "include": {}})

    # Mutate the source so its size/mtime no longer match the sidecar.
    with open(simple_book, "ab") as f:
        f.write(b"% trailing bytes\n")

    loaded = sidecar.load(scf, simple_book)
    assert loaded["overrides"]["starts"] == {}          # overrides ignored
    assert loaded["warnings"]                            # ...with a warning
    assert loaded["level"] == 2                          # settings still restored
    assert loaded["notebooklm"] is True
    assert loaded["band"].floor == 5000


def test_load_missing_sidecar_returns_none(simple_book, tmp_path):
    assert sidecar.load(_out(tmp_path), simple_book) is None
