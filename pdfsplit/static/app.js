"use strict";

/* pdfsplit boundary-review UI — vanilla JS, no framework, no external requests.
 *
 * The engine is the single source of truth: every correction is written to the sidecar
 * (POST /overrides) and the plan is then re-fetched, so the rendered rows always reflect
 * what `engine.plan_book` produced. Only flagged rows expand — that is the whole point.
 */

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, cls) => { const e = document.createElement(tag); if (cls) e.className = cls; return e; };

const state = {
  books: [],
  bookId: null,
  plan: null,
  overrides: { starts: {}, include: {} },
};

// ------------------------------------------------------------------- library

async function loadLibrary() {
  const data = await fetch("/api/library").then(r => r.json());
  state.books = data.books;
  $("#rail-dir").textContent = data.dir;
  const ul = $("#library");
  ul.innerHTML = "";
  for (const b of data.books) {
    const li = el("li");
    li.dataset.id = b.id;
    const title = el("span", "book-title");
    title.textContent = b.title;
    const meta = el("span", "book-meta");
    meta.textContent = `${b.filename} · ${b.pages} pp`;
    li.append(title, meta);
    if (!b.has_outline) {
      li.classList.add("no-outline");
      const tag = el("span", "tag");
      tag.textContent = "no outline";
      title.append(tag);
    } else {
      li.addEventListener("click", () => selectBook(b.id));
    }
    ul.append(li);
  }
}

function markActive() {
  for (const li of $("#library").children) {
    li.classList.toggle("active", li.dataset.id === state.bookId);
  }
}

// ------------------------------------------------------------------- options

function currentOptions() {
  return {
    level: parseInt($("#opt-level").value || "1", 10),
    notebooklm: $("#opt-notebooklm").checked,
    floor: parseInt($("#opt-floor").value || "6000", 10),
    target: parseInt($("#opt-target").value || "12000", 10),
    ceiling: parseInt($("#opt-ceiling").value || "20000", 10),
  };
}

function planQuery() {
  const o = currentOptions();
  return `level=${o.level}&notebooklm=${o.notebooklm}&floor=${o.floor}` +
         `&target=${o.target}&ceiling=${o.ceiling}`;
}

// --------------------------------------------------------------- select/load

async function selectBook(id) {
  state.bookId = id;
  markActive();
  $("#empty").hidden = true;
  $("#workspace").hidden = false;
  $("#split-result").hidden = true;
  const book = state.books.find(b => b.id === id);
  $("#book-title").textContent = book ? book.title : "";
  await loadPlan(true);
}

async function loadPlan(populateLevels) {
  const resp = await fetch(`/api/books/${state.bookId}/plan?${planQuery()}`);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    $("#chunks").innerHTML = `<p class="muted">Cannot plan this book: ${err.detail}</p>`;
    return;
  }
  const plan = await resp.json();
  state.plan = plan;
  state.overrides = plan.overrides || { starts: {}, include: {} };
  if (populateLevels) populateLevelSelect(plan);
  render(plan);
}

function populateLevelSelect(plan) {
  const sel = $("#opt-level");
  const levels = plan.available_levels && plan.available_levels.length
    ? plan.available_levels : [plan.level];
  sel.innerHTML = "";
  for (const lv of levels) {
    const opt = el("option");
    opt.value = String(lv);
    opt.textContent = `Level ${lv}`;
    sel.append(opt);
  }
  sel.value = String(plan.level);
}

// ------------------------------------------------------------------- render

function bandClass(words, band) {
  if (words < band.floor) return "under";
  if (words > band.ceiling) return "over";
  return "inband";
}

const STATUS_MARK = {
  ok: ["●", "ok"],
  snap_proposed: ["◑", "snap"],
  unverified: ["▲", "unver"],
  not_applicable: ["○", "na"],
};

function render(plan) {
  const book = state.books.find(b => b.id === state.bookId);
  const sub = [`${plan.total_pages} pages`, `level ${plan.level}`,
               plan.notebooklm ? "notebooklm merge" : "plain mode",
               plan.has_text_layer ? "text layer" : "no text layer"];
  $("#book-sub").textContent = sub.join(" · ");

  const emitted = plan.chunks.filter(c => c.skip_reason === null && c.include);
  const need = emitted.filter(c => c.verify.status === "snap_proposed"
                                || c.verify.status === "unverified");
  const verified = emitted.length - need.length;
  const summary = $("#summary");
  summary.innerHTML = `${verified} of ${emitted.length} auto-verified`;
  if (need.length) {
    summary.innerHTML += ` · <span class="need">${need.length} need review</span>`;
  }

  const warnBox = $("#warnings");
  warnBox.innerHTML = "";
  for (const w of plan.warnings || []) {
    const li = el("li"); li.textContent = w; warnBox.append(li);
  }

  const box = $("#chunks");
  box.innerHTML = "";
  for (const c of plan.chunks) box.append(renderChunk(c, plan));

  // Split gate: blocked while any flag is unresolved, unless "split anyway".
  const unresolved = need.length > 0;
  $("#split-btn").disabled = unresolved && !$("#opt-anyway").checked;
}

function renderChunk(c, plan) {
  const skipped = c.skip_reason !== null;
  const flagged = !skipped && c.include &&
    (c.verify.status === "snap_proposed" || c.verify.status === "unverified");

  const row = el("div", "chunk" + (skipped ? " skip" : flagged ? " flag" : ""));
  const line = el("div", "chunk-line");

  if (skipped) {
    const dot = el("span", "dot skip"); dot.textContent = "—";
    const label = el("span", "label"); label.textContent = c.label;
    const reason = el("span", "skip-reason"); reason.textContent = c.skip_reason;
    const inc = includeControl(c, "force include");
    line.append(dot, label, reason, inc);
    row.append(line);
    return row;
  }

  const [mark, klass] = STATUS_MARK[c.verify.status] || ["●", "na"];
  const dot = el("span", "dot " + klass);
  dot.textContent = mark;
  dot.title = c.verify.status;
  const seq = el("span", "seq"); seq.textContent = c.include ? String(c.seq) : "–";
  const label = el("span", "label"); label.textContent = c.label;
  const pages = el("span", "pages"); pages.textContent = `${c.start + 1}–${c.end + 1}`;
  const words = el("span", "words " + bandClass(c.words, plan.band));
  words.textContent = c.words.toLocaleString() + " w";
  words.title = `band ${plan.band.floor}/${plan.band.target}/${plan.band.ceiling}`;
  const inc = includeControl(c, "include");
  line.append(dot, seq, label, pages, words, inc);
  row.append(line);

  if (flagged) row.append(renderExpand(c));
  return row;
}

function includeControl(c, labelText) {
  const wrap = el("label", "inc");
  const cb = el("input");
  cb.type = "checkbox";
  cb.checked = c.include;
  cb.addEventListener("change", () => {
    if (!c.override_key) return;
    if (cb.checked) delete state.overrides.include[c.override_key];
    else state.overrides.include[c.override_key] = false;
    // A force-included skipped section: mark it kept.
    if (c.skip_reason !== null && cb.checked) state.overrides.include[c.override_key] = true;
    pushOverrides();
  });
  const txt = document.createTextNode(" " + labelText);
  wrap.append(cb, txt);
  return wrap;
}

function renderExpand(c) {
  const wrap = el("div", "expand");
  const strip = el("div", "strip");
  const total = state.plan.total_pages;
  for (let n = c.start - 2; n <= c.start + 2; n++) {
    if (n < 0 || n >= total) continue;
    const t = el("div", "thumb");
    if (n === c.start) t.classList.add("current");
    if (c.verify.proposed_start !== null && n === c.verify.proposed_start) {
      t.classList.add("proposed");
    }
    const img = el("img");
    img.loading = "lazy";
    img.src = `/api/books/${state.bookId}/page/${n}.png?w=240`;
    img.alt = `page ${n + 1}`;
    const cap = el("div", "cap");
    cap.textContent = `p.${n + 1}` +
      (n === c.start ? " · start" : "") +
      (n === c.verify.proposed_start ? " · proposed" : "");
    t.append(img, cap);
    strip.append(t);
  }
  wrap.append(strip);

  const controls = el("div", "controls");
  const left = el("button"); left.textContent = "◀";
  left.title = "move start one page earlier";
  left.addEventListener("click", () => nudge(c, -1));
  const right = el("button"); right.textContent = "▶";
  right.title = "move start one page later";
  right.addEventListener("click", () => nudge(c, +1));
  controls.append(left, right);

  if (c.verify.status === "snap_proposed" && c.verify.proposed_start !== null) {
    const accept = el("button", "accept");
    accept.textContent = `Accept p.${c.verify.proposed_start + 1}`;
    accept.addEventListener("click", () => setStart(c, c.verify.proposed_start));
    controls.append(accept);
  }
  const hint = el("span", "hint");
  hint.textContent = c.verify.status === "snap_proposed"
    ? "bookmark text may be one page off — check the thumbnails"
    : "could not confirm this boundary from the page text — check the thumbnails";
  controls.append(hint);
  wrap.append(controls);
  return wrap;
}

// --------------------------------------------------------------- corrections

function nudge(c, delta) {
  const target = c.start + delta;
  if (target < 0 || target >= state.plan.total_pages) return;
  setStart(c, target);
}

function setStart(c, newStart) {
  if (!c.override_key) return;
  state.overrides.starts[c.override_key] = newStart;
  pushOverrides();
}

async function pushOverrides() {
  await fetch(`/api/books/${state.bookId}/overrides`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state.overrides),
  });
  await loadPlan(false);
}

// -------------------------------------------------------------------- split

async function doSplit() {
  const o = currentOptions();
  const body = {
    ...o,
    prefix_book: $("#opt-prefix").checked,
    also_text: $("#opt-alsotext").checked,
    header_page: $("#opt-header").checked,
  };
  const btn = $("#split-btn");
  btn.disabled = true;
  btn.textContent = "Splitting…";
  try {
    const resp = await fetch(`/api/books/${state.bookId}/split`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const res = $("#split-result");
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      res.innerHTML = `<strong>Split failed:</strong> ${err.detail}`;
    } else {
      const data = await resp.json();
      res.innerHTML = `<strong>Wrote ${data.written.length} chunks</strong> to ` +
        `<code>${data.out_dir}</code>. Index: <code>${data.index}</code>`;
    }
    res.hidden = false;
  } finally {
    btn.textContent = "Split";
    render(state.plan);
  }
}

// --------------------------------------------------------------------- wire

function wireToolbar() {
  for (const id of ["opt-level", "opt-notebooklm", "opt-floor", "opt-target", "opt-ceiling"]) {
    $("#" + id).addEventListener("change", () => { if (state.bookId) loadPlan(false); });
  }
  $("#opt-anyway").addEventListener("change", () => { if (state.plan) render(state.plan); });
  $("#split-btn").addEventListener("click", doSplit);
}

wireToolbar();
loadLibrary();
