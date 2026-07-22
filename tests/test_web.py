"""Web UI tests (§10), driving the FastAPI app with ``TestClient`` over a temp library.

The library directory is just the shared ``tmp_path`` into which the conftest fixtures save
their synthetic PDFs — pointing the app at it yields a real multi-book library with no binary
fixtures committed. Every assertion mirrors an item in the prompt's test list; the last one
(source size + mtime unchanged after a full round trip) guards invariant §3.1.
"""

from __future__ import annotations

import os

import fitz
import pytest
from fastapi.testclient import TestClient

from pdfsplit import engine, sidecar
from pdfsplit.model import Band
from pdfsplit.web import create_app


@pytest.fixture
def library(simple_book, part_book, offset_book, no_outline_book, tmp_path):
    """A four-book library plus a separate output dir. Returns paths the tests need."""
    return {
        "dir": str(tmp_path),
        "out": str(tmp_path / "out"),
        "simple": simple_book,
        "part": part_book,
        "offset": offset_book,
        "no_outline": no_outline_book,
    }


@pytest.fixture
def client(library):
    app = create_app(library["dir"], library["out"])
    with TestClient(app) as c:
        yield c


def _id_for(client, filename: str) -> str:
    books = client.get("/api/library").json()["books"]
    return next(b["id"] for b in books if b["filename"] == filename)


def _slug_of(path: str) -> str:
    doc = fitz.open(path)
    try:
        title, _ = engine.derive_title(doc, path)
    finally:
        doc.close()
    return engine.slugify(title)


# --------------------------------------------------------------------------- #

def test_library_lists_fixtures_with_outline_flag(client, library):
    data = client.get("/api/library").json()
    assert data["dir"] == library["dir"]
    by_name = {b["filename"]: b for b in data["books"]}

    assert "simple_book.pdf" in by_name
    assert "no_outline_book.pdf" in by_name
    assert by_name["no_outline_book.pdf"]["has_outline"] is False
    assert by_name["simple_book.pdf"]["has_outline"] is True
    assert by_name["simple_book.pdf"]["pages"] == 60


def test_plan_matches_engine(client, library):
    book_id = _id_for(client, "simple_book.pdf")
    api = client.get(f"/api/books/{book_id}/plan").json()
    direct = engine.plan_book(library["simple"], level=1)

    got = [(c["start"], c["end"], c["label"]) for c in api["chunks"]]
    want = [(c.start, c.end, c.label) for c in direct.chunks]
    assert got == want            # the API must not drift from the engine
    assert api["available_levels"] == [1]


def test_level_and_notebooklm_change_the_plan(client, library):
    book_id = _id_for(client, "part_book.pdf")

    lvl1 = client.get(f"/api/books/{book_id}/plan", params={"level": 1}).json()
    lvl2 = client.get(f"/api/books/{book_id}/plan", params={"level": 2}).json()
    assert lvl1["available_levels"] == [1, 2]
    # Different outline depth => different boundaries.
    assert [c["label"] for c in lvl1["chunks"]] != [c["label"] for c in lvl2["chunks"]]

    plain = client.get(f"/api/books/{book_id}/plan",
                       params={"level": 2, "notebooklm": "false"}).json()
    merged = client.get(f"/api/books/{book_id}/plan",
                        params={"level": 2, "notebooklm": "true"}).json()
    # The merge pass folds Part I's short chapters, so fewer emitted chunks.
    emitted = lambda p: [c for c in p["chunks"] if c["skip_reason"] is None and c["include"]]
    assert len(emitted(merged)) < len(emitted(plain))


def test_thumbnail_is_png_and_out_of_range_is_404(client):
    book_id = _id_for(client, "simple_book.pdf")
    ok = client.get(f"/api/books/{book_id}/page/0.png", params={"w": 200})
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "image/png"
    assert ok.content[:4] == b"\x89PNG"

    assert client.get(f"/api/books/{book_id}/page/9999.png").status_code == 404


def test_unknown_and_forged_ids_are_404(client):
    assert client.get("/api/books/deadbeefcafe/plan").status_code == 404
    # A path-like id must not resolve to a file (path-traversal guard).
    forged = client.get("/api/books/..%2F..%2Fetc%2Fpasswd/plan")
    assert forged.status_code == 404
    assert client.get("/api/books/..%2F..%2Fetc%2Fpasswd/page/0.png").status_code == 404


def test_overrides_write_sidecar_and_are_reflected(client, library):
    book_id = _id_for(client, "offset_book.pdf")
    plan = client.get(f"/api/books/{book_id}/plan").json()

    flagged = next(c for c in plan["chunks"] if c["verify"]["status"] == "snap_proposed")
    key = flagged["override_key"]
    proposed = flagged["verify"]["proposed_start"]
    assert proposed is not None

    resp = client.post(f"/api/books/{book_id}/overrides",
                       json={"starts": {key: proposed}, "include": {}})
    assert resp.status_code == 200

    scf = sidecar.sidecar_path(library["out"], _slug_of(library["offset"]))
    assert os.path.exists(scf)                       # sidecar written immediately

    after = client.get(f"/api/books/{book_id}/plan").json()
    corrected = next(c for c in after["chunks"] if c["override_key"] == key)
    assert corrected["start"] == proposed
    assert corrected["verify"]["status"] == "ok"     # accepted correction reads as verified


def test_split_writes_files_into_out_dir(client, library):
    book_id = _id_for(client, "simple_book.pdf")
    resp = client.post(f"/api/books/{book_id}/split", json={
        "level": 1, "notebooklm": False,
        "floor": 6000, "target": 12000, "ceiling": 20000,
        "prefix_book": False, "also_text": False, "header_page": True,
    })
    assert resp.status_code == 200
    data = resp.json()

    assert data["out_dir"].startswith(library["out"])
    assert data["written"]
    for path in data["written"]:
        assert os.path.exists(path)
    assert os.path.exists(data["index"])


def test_source_pdfs_unchanged_after_round_trip(client, library):
    sources = [library["simple"], library["part"], library["offset"], library["no_outline"]]
    before = {p: os.stat(p) for p in sources}

    # A full round trip: list, plan, thumbnail, accept a correction, split.
    client.get("/api/library")
    book_id = _id_for(client, "offset_book.pdf")
    plan = client.get(f"/api/books/{book_id}/plan").json()
    client.get(f"/api/books/{book_id}/page/2.png")
    flagged = next(c for c in plan["chunks"] if c["verify"]["status"] == "snap_proposed")
    client.post(f"/api/books/{book_id}/overrides",
                json={"starts": {flagged["override_key"]: flagged["verify"]["proposed_start"]},
                      "include": {}})
    client.post(f"/api/books/{book_id}/split", json={"level": 1, "header_page": True})

    for p in sources:
        after = os.stat(p)
        assert after.st_size == before[p].st_size, p
        assert after.st_mtime == before[p].st_mtime, p
