"use strict";

// Multi-audio Local Transcriber.
//
// Each audio is a "job" with its own state, its own live SSE stream, a tab, and
// a row in the status rail. Only one job is focused at a time; the focused view
// (player, toolbar, transcript) is rendered from that job's state. Background
// jobs keep streaming into their own state and update their tab and rail live,
// so you can queue several files and watch them all progress.

const $ = (id) => document.getElementById(id);

// --- topbar / upload elements ------------------------------------------------
const devBadge = $("devbadge"), themeToggle = $("themeToggle"), addFilesBtn = $("addFiles");
const uploadCard = $("uploadCard");
const dz = $("dropzone"), fileInput = $("file"), chipsWrap = $("filechips");
const go = $("go"), modelSel = $("model"), langSel = $("language");

// --- workspace / focused-view elements ---------------------------------------
const workspace = $("workspace"), tabbar = $("tabbar");
const railList = $("railList"), railCount = $("railCount");
const progPanel = $("progress"), deviceEl = $("device"), statusEl = $("status");
const elapsedEl = $("elapsed"), etaEl = $("eta"), fill = $("fill");
const resultEl = $("result"), playerWrap = $("playerWrap"), toolbar = $("toolbar");
const rtitle = $("rtitle"), rmeta = $("rmeta"), folderEl = $("folder");
const transcriptEl = $("transcript");
const searchEl = $("search"), searchCount = $("searchCount");
const searchPrev = $("searchPrev"), searchNext = $("searchNext");
const editToggle = $("editToggle"), copyBtn = $("copy"), copyTimesBtn = $("copyTimes");

// --- state -------------------------------------------------------------------
const jobs = new Map();   // id -> job
let activeId = null;      // focused job id
let staged = [];          // File[] waiting for the Transcribe click
let activeMediaEl = null; // <audio>/<video> of the focused job
let activeMediaURL = null;
let activeIdx = -1;       // highlighted segment index in the focused player

// --- helpers -----------------------------------------------------------------
function humanSize(b) {
  if (b < 1024) return b + " B";
  const u = ["KB", "MB", "GB"]; let i = -1;
  do { b /= 1024; i++; } while (b >= 1024 && i < u.length - 1);
  return b.toFixed(b < 10 ? 1 : 0) + " " + u[i];
}
function mmss(s) {
  s = Math.max(0, Math.floor(s));
  const m = Math.floor(s / 60), ss = s % 60;
  return m + ":" + String(ss).padStart(2, "0");
}
function ts(seconds, sep) {
  if (seconds < 0) seconds = 0;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  let ms = Math.round((seconds - Math.floor(seconds)) * 1000);
  let ss = s;
  if (ms === 1000) { ss += 1; ms = 0; }
  const p = (n, w) => String(n).padStart(w, "0");
  return p(h, 2) + ":" + p(m, 2) + ":" + p(ss, 2) + (sep || ",") + p(ms, 3);
}
function escapeHtml(t) {
  return t.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function safeStem(name) {
  const base = (name || "transcript").replace(/\.[^.]+$/, "");
  const clean = base.replace(/[^A-Za-z0-9 ._-]+/g, "_").trim();
  return (clean || "transcript").slice(0, 80);
}
function toast(msg, isErr) {
  let t = $("toast");
  if (!t) {
    t = document.createElement("div");
    t.id = "toast";
    t.style.cssText = "position:fixed;left:50%;bottom:28px;transform:translateX(-50%);" +
      "background:#161a23;color:#fff;padding:10px 16px;border-radius:10px;font-size:14px;" +
      "box-shadow:0 6px 24px rgba(0,0,0,.25);z-index:50;max-width:90vw;opacity:0;transition:opacity .2s";
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.style.background = isErr ? "#b42318" : "#161a23";
  t.style.opacity = "1";
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.style.opacity = "0"; }, 2600);
}
function shortDevice(note) { return String(note).split(" -- ")[0].trim(); }

// --- theme -------------------------------------------------------------------
function applyThemeLabel() {
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  themeToggle.textContent = dark ? "Light" : "Dark";
}
themeToggle.addEventListener("click", () => {
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  if (dark) document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", "dark");
  try { localStorage.setItem("lt-theme", dark ? "light" : "dark"); } catch (e) {}
  applyThemeLabel();
});
applyThemeLabel();

// --- device badge ------------------------------------------------------------
function applyDeviceBadge(el, note) {
  el.textContent = shortDevice(note);
  el.title = note;
  el.classList.toggle("cpu", /cpu/i.test(note));
  el.classList.remove("idle");
}
fetch("/api/device")
  .then((r) => r.json())
  .then((d) => { if (d && d.device) applyDeviceBadge(devBadge, d.device); })
  .catch(() => { devBadge.textContent = "hardware unknown"; devBadge.classList.remove("idle"); });

// --- file staging ------------------------------------------------------------
const NON_MEDIA = /\.(txt|pdf|docx?|xlsx?|pptx?|zip|rar|7z|gz|tar|exe|dll|msi|bat|png|jpe?g|gif|bmp|svg|webp|ico|csv|json|html?|md|py|js|css)$/i;

function addStaged(fileList) {
  const files = Array.from(fileList || []);
  for (const f of files) {
    if (NON_MEDIA.test(f.name)) { toast(f.name + " is not a media file, skipped.", true); continue; }
    // de-dupe by name+size so a double drop does not stage twice
    if (staged.some((s) => s.name === f.name && s.size === f.size)) continue;
    staged.push(f);
  }
  renderChips();
}
function removeStaged(idx) { staged.splice(idx, 1); renderChips(); }
function renderChips() {
  chipsWrap.innerHTML = "";
  chipsWrap.hidden = staged.length === 0;
  staged.forEach((f, i) => {
    const chip = document.createElement("span");
    chip.className = "filechip";
    const name = document.createElement("span");
    name.className = "fc-name"; name.textContent = f.name;
    const size = document.createElement("span");
    size.className = "fc-size"; size.textContent = humanSize(f.size);
    const x = document.createElement("button");
    x.className = "fc-x"; x.title = "Remove"; x.setAttribute("aria-label", "Remove file");
    x.innerHTML = "&times;";
    x.addEventListener("click", (e) => { e.stopPropagation(); removeStaged(i); });
    chip.appendChild(name); chip.appendChild(size); chip.appendChild(x);
    chipsWrap.appendChild(chip);
  });
  go.disabled = staged.length === 0;
  go.textContent = staged.length > 1 ? ("Transcribe " + staged.length + " files") : "Transcribe";
}
dz.addEventListener("click", () => fileInput.click());
dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
fileInput.addEventListener("change", () => { addStaged(fileInput.files); fileInput.value = ""; });
["dragenter", "dragover"].forEach((ev) =>
  dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
["dragleave", "drop"].forEach((ev) =>
  dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
dz.addEventListener("drop", (e) => { if (e.dataTransfer.files) addStaged(e.dataTransfer.files); });

// --- upload card visibility --------------------------------------------------
function showUploadCard(show) {
  uploadCard.hidden = !show;
  addFilesBtn.hidden = !(jobs.size > 0 && !show);
}
addFilesBtn.addEventListener("click", () => showUploadCard(true));

// --- start jobs --------------------------------------------------------------
go.addEventListener("click", () => {
  if (!staged.length) return;
  const model = modelSel.value, lang = langSel.value;
  const batch = staged.slice();
  staged = []; renderChips();
  showUploadCard(false);
  workspace.hidden = false;
  let firstNewId = null;
  for (const file of batch) {
    const id = startJob(file, model, lang);
    if (id && !firstNewId) firstNewId = id;
  }
  if (firstNewId) setActive(firstNewId);
  updateRailCount();
});

function startJob(file, model, lang) {
  const job = {
    id: "pending-" + Math.random().toString(36).slice(2),
    file, name: file.name, model, lang,
    status: "uploading", segments: [], duration: 0,
    detectedLang: "", device: "", progress: 0,
    startedAt: 0, finishedInMs: 0,
    es: null, errorMsg: "",
    editing: false, searchQuery: "",
    tabEl: null, railEl: null,
  };
  jobs.set(job.id, job);
  addTabAndRail(job);

  const fd = new FormData();
  fd.append("file", file);
  fd.append("model", model);
  fd.append("language", lang);

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/jobs");
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      job.uploadPct = Math.round((e.loaded / e.total) * 100);
      if (job.status === "uploading") { updateTab(job); updateRail(job); if (job.id === activeId) renderStatusLine(job); }
    }
  };
  xhr.onload = () => {
    if (xhr.status !== 200) { failJob(job, serverError(xhr)); return; }
    let data; try { data = JSON.parse(xhr.responseText); } catch (e) { failJob(job, "Bad server response."); return; }
    // Re-key the job under the real server id.
    const realId = data.job_id;
    jobs.delete(job.id);
    const wasActive = activeId === job.id;
    job.id = realId;
    jobs.set(realId, job);
    if (wasActive) activeId = realId;
    streamJob(job);
  };
  xhr.onerror = () => failJob(job, "Upload error (is the server running?).");
  xhr.send(fd);
  return job.id;
}

function serverError(xhr) {
  try { const j = JSON.parse(xhr.responseText); if (j && j.detail) return j.detail; } catch (e) {}
  return "Upload failed: " + xhr.status + " " + (xhr.statusText || "");
}

// --- per-job SSE -------------------------------------------------------------
function streamJob(job) {
  const es = new EventSource("/api/jobs/" + job.id + "/events");
  job.es = es;
  es.onmessage = (e) => {
    let ev; try { ev = JSON.parse(e.data); } catch (err) { return; }
    handleEvent(job, ev);
  };
  es.addEventListener("end", () => { es.close(); job.es = null; });
  es.onerror = () => { /* keep partial transcript; the end event closes us normally */ };
}

function handleEvent(job, ev) {
  if (ev.type === "queued") {
    job.status = "queued";
  } else if (ev.type === "status") {
    if (job.status === "queued" || job.status === "uploading") {
      job.status = "running";
      if (!job.startedAt) job.startedAt = Date.now();
    }
    if (ev.device) job.device = ev.device;
    job.statusMsg = ev.message || "";
  } else if (ev.type === "language") {
    job.detectedLang = ev.language || "";
    job.statusMsg = "Detected language: " + (ev.language || "?") +
      " (" + Math.round((ev.probability || 0) * 100) + "%)";
  } else if (ev.type === "segment") {
    if (!job.startedAt) job.startedAt = Date.now();
    job.status = "running";
    job.progress = ev.progress || 0;
    const seg = { start: ev.start, end: ev.end, text: ev.text };
    job.segments.push(seg);
    if (job.id === activeId) appendSegmentDOM(job, seg);
  } else if (ev.type === "done") {
    job.status = "done";
    job.progress = 1;
    if (ev.duration) job.duration = ev.duration;
    if (job.startedAt) job.finishedInMs = Date.now() - job.startedAt;
    if (Array.isArray(ev.segments) && ev.segments.length && !job.segments.length) {
      job.segments = ev.segments.map((s) => ({ start: s.start, end: s.end, text: s.text }));
    }
    fetchFolder(job);
  } else if (ev.type === "error") {
    failJob(job, ev.message || "Transcription failed.");
    return;
  }
  updateTab(job);
  updateRail(job);
  updateRailCount();
  if (job.id === activeId) renderFocusLive(job);
}

function failJob(job, msg) {
  job.status = "error";
  job.errorMsg = msg;
  updateTab(job);
  updateRail(job);
  updateRailCount();
  if (job.id === activeId) renderFocusLive(job);
}

function fetchFolder(job) {
  fetch("/api/jobs/" + job.id + "/folder").then((r) => r.json())
    .then((d) => { if (d && d.folder) { job.folder = d.folder; if (job.id === activeId) folderEl.textContent = "Saved to: " + d.folder; } })
    .catch(() => {});
}

// --- tabs + rail -------------------------------------------------------------
function statusLabel(job) {
  if (job.status === "uploading") return "uploading " + (job.uploadPct || 0) + "%";
  if (job.status === "queued") return "queued";
  if (job.status === "running") return Math.round((job.progress || 0) * 100) + "%";
  if (job.status === "done") return "done";
  if (job.status === "error") return "error";
  return job.status;
}
function addTabAndRail(job) {
  // tab
  const tab = document.createElement("button");
  tab.className = "tab";
  tab.setAttribute("role", "tab");
  tab.innerHTML =
    '<span class="tab-dot"></span>' +
    '<span class="tab-name"></span>' +
    '<span class="tab-pct"></span>' +
    '<span class="tab-x" title="Close" aria-label="Close">&times;</span>';
  tab.querySelector(".tab-name").textContent = job.name;
  tab.title = job.name;
  tab.addEventListener("click", (e) => {
    if (e.target.classList.contains("tab-x")) { e.stopPropagation(); closeJob(job.id); return; }
    setActive(job.id);
  });
  tabbar.appendChild(tab);
  job.tabEl = tab;

  // rail row
  const row = document.createElement("div");
  row.className = "rail-row";
  row.innerHTML =
    '<span class="rail-dot"></span>' +
    '<span class="rail-name"></span>' +
    '<span class="rail-bar"><span class="rail-fill"></span></span>' +
    '<span class="rail-meta"></span>';
  row.querySelector(".rail-name").textContent = job.name;
  row.title = job.name;
  row.addEventListener("click", () => setActive(job.id));
  railList.appendChild(row);
  job.railEl = row;

  updateTab(job);
  updateRail(job);
  updateRailCount();
}
function updateTab(job) {
  const t = job.tabEl; if (!t) return;
  t.dataset.status = job.status;
  t.classList.toggle("active", job.id === activeId);
  const pct = t.querySelector(".tab-pct");
  if (job.status === "running") pct.textContent = Math.round((job.progress || 0) * 100) + "%";
  else if (job.status === "uploading") pct.textContent = (job.uploadPct || 0) + "%";
  else pct.textContent = "";
  pct.classList.toggle("spin", job.status === "queued");
  if (job.status === "queued") pct.textContent = "...";
}
function updateRail(job) {
  const r = job.railEl; if (!r) return;
  r.dataset.status = job.status;
  r.classList.toggle("active", job.id === activeId);
  const p = job.status === "done" ? 1 : (job.status === "running" ? (job.progress || 0) : 0);
  r.querySelector(".rail-fill").style.width = Math.round(p * 100) + "%";
  r.querySelector(".rail-meta").textContent = statusLabel(job);
}
function updateRailCount() {
  const n = jobs.size;
  const done = Array.from(jobs.values()).filter((j) => j.status === "done").length;
  railCount.textContent = n ? (done + " / " + n + " done") : "";
}
function closeJob(id) {
  const job = jobs.get(id); if (!job) return;
  if (job.es) { try { job.es.close(); } catch (e) {} }
  if (job.tabEl) job.tabEl.remove();
  if (job.railEl) job.railEl.remove();
  jobs.delete(id);
  if (activeId === id) {
    activeId = null;
    const next = jobs.size ? Array.from(jobs.keys())[jobs.size - 1] : null;
    if (next) setActive(next);
    else { workspace.hidden = true; showUploadCard(true); }
  }
  updateRailCount();
}

// --- focused view ------------------------------------------------------------
function setActive(id) {
  const job = jobs.get(id); if (!job) return;
  activeId = id;
  jobs.forEach((j) => { if (j.tabEl) j.tabEl.classList.toggle("active", j.id === id); if (j.railEl) j.railEl.classList.toggle("active", j.id === id); });
  renderActive();
}

function renderActive() {
  const job = jobs.get(activeId);
  // tear down previous player
  if (activeMediaURL) { URL.revokeObjectURL(activeMediaURL); activeMediaURL = null; }
  playerWrap.innerHTML = ""; playerWrap.hidden = true; activeMediaEl = null; activeIdx = -1;
  transcriptEl.innerHTML = ""; transcriptEl.classList.remove("editing");
  if (!job) { resultEl.hidden = true; progPanel.hidden = true; return; }

  resultEl.hidden = false;

  // transcript body (rebuild from state)
  rtitle.textContent = job.status === "done" ? ("Transcript: " + job.name) : job.name;
  folderEl.textContent = job.folder ? ("Saved to: " + job.folder) : "";
  job.segments.forEach((seg) => transcriptEl.appendChild(makeSegEl(job, seg)));

  // player (only for the focused job)
  buildPlayer(job);

  // toolbar visible once there is anything to act on
  toolbar.hidden = job.segments.length === 0;
  editToggle.setAttribute("aria-pressed", job.editing ? "true" : "false");
  editToggle.textContent = job.editing ? "Done" : "Edit";
  transcriptEl.classList.toggle("editing", job.editing);
  job.segments.forEach((s) => { if (s.textEl) s.textEl.contentEditable = job.editing ? "true" : "false"; });
  searchEl.value = job.searchQuery || "";
  runSearch(job.searchQuery || "");

  renderFocusLive(job);
}

// Update the progress panel + meta for the focused job without rebuilding DOM.
function renderFocusLive(job) {
  if (!job || job.id !== activeId) return;
  renderStatusLine(job);
  if (job.status === "done") {
    progPanel.hidden = true;
    const count = job.segments.length;
    const dur = job.duration || (job.segments.length ? job.segments[job.segments.length - 1].end : 0);
    const bits = [count + " segment" + (count === 1 ? "" : "s"), mmss(dur) + " of audio"];
    if (job.finishedInMs) bits.push("done in " + mmss(job.finishedInMs / 1000));
    if (job.detectedLang) bits.unshift(job.detectedLang.toUpperCase());
    rmeta.textContent = bits.join("  ·  ");
    rtitle.textContent = "Transcript: " + job.name;
    toolbar.hidden = job.segments.length === 0;
  } else if (job.status === "error") {
    progPanel.hidden = false;
    fill.classList.remove("indet"); fill.style.width = "0";
    statusEl.innerHTML = '<span class="err">' + escapeHtml(job.errorMsg || "Failed.") + "</span>";
    etaEl.textContent = ""; rmeta.textContent = "";
  } else {
    // uploading / queued / running
    progPanel.hidden = false;
    toolbar.hidden = job.segments.length === 0;
  }
}

function renderStatusLine(job) {
  if (job.device) { deviceEl.textContent = shortDevice(job.device); deviceEl.title = job.device; deviceEl.classList.toggle("cpu", /cpu/i.test(job.device)); }
  else { deviceEl.textContent = "preparing..."; }
  if (job.status === "uploading") {
    fill.classList.add("indet"); fill.style.width = "";
    statusEl.textContent = "Uploading " + (job.uploadPct || 0) + "%"; etaEl.textContent = "";
  } else if (job.status === "queued") {
    fill.classList.add("indet"); fill.style.width = "";
    statusEl.textContent = "Queued, waiting for a free worker..."; etaEl.textContent = "";
  } else if (job.status === "running") {
    const p = job.progress || 0;
    if (p > 0) { fill.classList.remove("indet"); fill.style.width = Math.round(p * 100) + "%"; }
    else { fill.classList.add("indet"); fill.style.width = ""; }
    statusEl.textContent = job.statusMsg || ("Transcribing " + Math.round(p * 100) + "%");
    showEta(job);
  }
}
function showEta(job) {
  const p = job.progress || 0;
  if (!job.startedAt || p <= 0.02 || p >= 1) { etaEl.textContent = ""; return; }
  const elapsed = (Date.now() - job.startedAt) / 1000;
  const remaining = elapsed * (1 - p) / p;
  etaEl.textContent = "~" + mmss(remaining) + " left";
}

// A single ticker refreshes the focused job's elapsed clock + rail meta.
setInterval(() => {
  const job = jobs.get(activeId);
  if (job && job.status === "running" && job.startedAt) {
    elapsedEl.textContent = mmss((Date.now() - job.startedAt) / 1000);
  }
}, 500);

// --- transcript rendering (focused job) --------------------------------------
function makeSegEl(job, seg) {
  const row = document.createElement("div");
  row.className = "seg";
  const t = document.createElement("span");
  t.className = "t"; t.textContent = mmss(seg.start); t.title = "Jump to " + mmss(seg.start);
  const text = document.createElement("span");
  text.className = "seg-text"; text.textContent = seg.text;
  text.contentEditable = job.editing ? "true" : "false";
  row.appendChild(t); row.appendChild(text);
  t.addEventListener("click", (e) => { e.stopPropagation(); seekTo(seg.start); });
  row.addEventListener("click", () => { if (!job.editing) seekTo(seg.start); });
  text.addEventListener("input", () => { seg.text = text.textContent; });
  seg.el = row; seg.textEl = text;
  return row;
}
function appendSegmentDOM(job, seg) {
  transcriptEl.appendChild(makeSegEl(job, seg));
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
  if (toolbar.hidden) toolbar.hidden = false;
}

// --- media player + sync (focused job) ---------------------------------------
function isVideoFile(f) {
  if (f.type && f.type.startsWith("video")) return true;
  if (f.type && f.type.startsWith("audio")) return false;
  return /\.(mp4|mov|mkv|webm|avi|m4v|mpg|mpeg|ogv|ts|3gp)$/i.test(f.name);
}
function buildPlayer(job) {
  playerWrap.innerHTML = ""; activeMediaEl = null;
  if (!job || !job.file) { playerWrap.hidden = true; return; }
  const el = document.createElement(isVideoFile(job.file) ? "video" : "audio");
  el.id = "player"; el.controls = true; el.preload = "metadata";
  activeMediaURL = URL.createObjectURL(job.file);
  el.src = activeMediaURL;
  el.addEventListener("timeupdate", onTimeUpdate);
  el.addEventListener("error", () => {
    playerWrap.innerHTML =
      '<div class="player-note">Preview is not available for this file format in the browser. ' +
      "Transcription, editing, and export still work.</div>";
    activeMediaEl = null;
  });
  playerWrap.appendChild(el);
  playerWrap.hidden = false;
  activeMediaEl = el;
}
function seekTo(t) {
  if (!activeMediaEl) return;
  try { activeMediaEl.currentTime = Math.max(0, t); activeMediaEl.play().catch(() => {}); } catch (e) {}
}
function onTimeUpdate() {
  const job = jobs.get(activeId);
  if (!activeMediaEl || !job || !job.segments.length) return;
  const t = activeMediaEl.currentTime;
  let idx = -1;
  for (let i = 0; i < job.segments.length; i++) {
    if (job.segments[i].start <= t + 0.001) idx = i; else break;
  }
  if (idx === activeIdx) return;
  if (activeIdx >= 0 && job.segments[activeIdx] && job.segments[activeIdx].el) job.segments[activeIdx].el.classList.remove("active");
  activeIdx = idx;
  if (idx >= 0 && job.segments[idx] && job.segments[idx].el) {
    job.segments[idx].el.classList.add("active");
    if (!activeMediaEl.paused) job.segments[idx].el.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

// --- search (focused job) ----------------------------------------------------
let matches = [], matchIndex = -1;
function clearHighlights(job) {
  job.segments.forEach((s) => { if (s.textEl) s.textEl.textContent = s.text; });
  matches = []; matchIndex = -1;
}
function runSearch(q) {
  const job = jobs.get(activeId); if (!job) return;
  job.searchQuery = q || "";
  clearHighlights(job);
  q = (q || "").trim();
  if (!q) { searchCount.textContent = ""; searchPrev.hidden = searchNext.hidden = true; return; }
  const ql = q.toLowerCase();
  job.segments.forEach((s) => {
    if (!s.textEl) return;
    const text = s.text, low = text.toLowerCase();
    if (low.indexOf(ql) === -1) return;
    let html = "", i = 0, idx;
    while ((idx = low.indexOf(ql, i)) !== -1) {
      html += escapeHtml(text.slice(i, idx));
      html += '<mark class="hit">' + escapeHtml(text.slice(idx, idx + q.length)) + "</mark>";
      i = idx + q.length;
    }
    html += escapeHtml(text.slice(i));
    s.textEl.innerHTML = html;
    s.textEl.querySelectorAll("mark.hit").forEach((m) => matches.push(m));
  });
  searchCount.textContent = matches.length
    ? (matches.length + " match" + (matches.length > 1 ? "es" : ""))
    : "no matches";
  searchPrev.hidden = searchNext.hidden = matches.length < 2;
  if (matches.length) { matchIndex = 0; focusMatch(); }
}
function focusMatch() {
  matches.forEach((m) => m.classList.remove("current"));
  const m = matches[matchIndex];
  if (!m) return;
  m.classList.add("current");
  m.scrollIntoView({ block: "center", behavior: "smooth" });
}
searchEl.addEventListener("input", () => runSearch(searchEl.value));
searchEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && matches.length) {
    e.preventDefault();
    matchIndex = (matchIndex + (e.shiftKey ? -1 : 1) + matches.length) % matches.length;
    focusMatch();
  }
});
searchNext.addEventListener("click", () => { if (matches.length) { matchIndex = (matchIndex + 1) % matches.length; focusMatch(); } });
searchPrev.addEventListener("click", () => { if (matches.length) { matchIndex = (matchIndex - 1 + matches.length) % matches.length; focusMatch(); } });

// --- edit (focused job) ------------------------------------------------------
editToggle.addEventListener("click", () => {
  const job = jobs.get(activeId); if (!job) return;
  job.editing = !job.editing;
  editToggle.setAttribute("aria-pressed", job.editing ? "true" : "false");
  editToggle.textContent = job.editing ? "Done" : "Edit";
  transcriptEl.classList.toggle("editing", job.editing);
  if (job.editing && searchEl.value) { searchEl.value = ""; runSearch(""); }
  job.segments.forEach((s) => { if (s.textEl) s.textEl.contentEditable = job.editing ? "true" : "false"; });
});

// --- exports + copy (focused job) --------------------------------------------
function plainSegments(job) { return job.segments.map((s) => ({ start: s.start, end: s.end, text: (s.text || "").trim() })); }
function buildTxt(segs) { return segs.map((s) => s.text).join("\n").trim() + "\n"; }
function buildTextWithTimes(segs) { return segs.map((s) => "[" + mmss(s.start) + "] " + s.text).join("\n").trim() + "\n"; }
function buildSrt(segs) {
  const out = [];
  segs.forEach((s, i) => { out.push(String(i + 1), ts(s.start) + " --> " + ts(s.end), s.text, ""); });
  return out.join("\n");
}
function buildVtt(segs) {
  const out = ["WEBVTT", ""];
  segs.forEach((s) => { out.push(ts(s.start, ".") + " --> " + ts(s.end, "."), s.text, ""); });
  return out.join("\n");
}
function buildMd(segs, title) {
  const out = ["# " + title, ""];
  segs.forEach((s) => { out.push("**[" + mmss(s.start) + "]** " + s.text, ""); });
  return out.join("\n").trim() + "\n";
}
function buildJson(segs, duration, title) {
  return JSON.stringify({
    title: title,
    duration: Math.round((duration || 0) * 100) / 100,
    segment_count: segs.length,
    segments: segs.map((s) => ({
      start: Math.round(s.start * 1000) / 1000,
      end: Math.round(s.end * 1000) / 1000,
      text: s.text,
    })),
  }, null, 2) + "\n";
}
function downloadBlob(filename, text, mime) {
  const blob = new Blob([text], { type: mime || "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}
async function exportFormat(fmt) {
  const job = jobs.get(activeId); if (!job) return;
  const segs = plainSegments(job);
  const base = safeStem(job.name);
  if (fmt === "txt") return downloadBlob(base + ".txt", buildTxt(segs));
  if (fmt === "srt") return downloadBlob(base + ".srt", buildSrt(segs), "text/plain;charset=utf-8");
  if (fmt === "vtt") return downloadBlob(base + ".vtt", buildVtt(segs), "text/vtt;charset=utf-8");
  if (fmt === "md") return downloadBlob(base + ".md", buildMd(segs, base), "text/markdown;charset=utf-8");
  if (fmt === "json") {
    const dur = job.duration || (segs.length ? segs[segs.length - 1].end : 0);
    return downloadBlob(base + ".json", buildJson(segs, dur, base), "application/json");
  }
  if (fmt === "docx") return exportDocx(segs, base);
}
async function exportDocx(segs, base) {
  const btn = $("dl-docx");
  btn.classList.add("busy");
  try {
    const res = await fetch("/api/export/docx", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ segments: segs, title: base, with_timestamps: true }),
    });
    if (!res.ok) throw new Error("server " + res.status);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = base + ".docx";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
  } catch (e) {
    toast("DOCX export failed: " + e.message, true);
  } finally {
    btn.classList.remove("busy");
  }
}
["txt", "srt", "vtt", "md", "json", "docx"].forEach((fmt) => {
  $("dl-" + fmt).addEventListener("click", () => exportFormat(fmt));
});
function copyText(text, btn) {
  navigator.clipboard.writeText(text).then(() => {
    const old = btn.textContent;
    btn.textContent = "Copied";
    setTimeout(() => (btn.textContent = old), 1400);
  }).catch(() => toast("Copy failed", true));
}
copyBtn.addEventListener("click", () => { const j = jobs.get(activeId); if (j) copyText(buildTxt(plainSegments(j)), copyBtn); });
copyTimesBtn.addEventListener("click", () => { const j = jobs.get(activeId); if (j) copyText(buildTextWithTimes(plainSegments(j)), copyTimesBtn); });
