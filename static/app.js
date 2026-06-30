"use strict";

const $ = (id) => document.getElementById(id);

// Elements (IDs preserve the proven upload/stream/progress contract).
const uploadCard = $("uploadCard");
const dz = $("dropzone"), fileInput = $("file"), chip = $("filechip");
const fnEl = $("filename"), fsEl = $("filesize"), clearBtn = $("clearfile");
const go = $("go"), modelSel = $("model"), langSel = $("language");
const progPanel = $("progress"), deviceEl = $("device"), statusEl = $("status");
const elapsedEl = $("elapsed"), etaEl = $("eta"), fill = $("fill");
const devBadge = $("devbadge"), themeToggle = $("themeToggle"), newFileBtn = $("newFile");
const resultEl = $("result"), playerWrap = $("playerWrap"), toolbar = $("toolbar");
const rtitle = $("rtitle"), rmeta = $("rmeta"), folderEl = $("folder");
const transcriptEl = $("transcript");
const searchEl = $("search"), searchCount = $("searchCount");
const searchPrev = $("searchPrev"), searchNext = $("searchNext");
const editToggle = $("editToggle"), copyBtn = $("copy"), copyTimesBtn = $("copyTimes");

// State
let currentFile = null;
let timer = null, startedAt = 0;
let segments = [];          // {start, end, text, el, textEl}
let mediaEl = null, mediaURL = null, activeIdx = -1;
let editing = false;
let matches = [], matchIndex = -1;
let detectedLang = "";

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
function shortDevice(note) { return String(note).split(" -- ")[0].trim(); }
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

// --- file selection ----------------------------------------------------------
function setFile(f) {
  if (!f) return;
  currentFile = f;
  fnEl.textContent = f.name;
  fsEl.textContent = humanSize(f.size);
  chip.hidden = false;
  go.disabled = false;
}
function clearFile() {
  currentFile = null; fileInput.value = "";
  chip.hidden = true; go.disabled = true;
}
dz.addEventListener("click", (e) => { if (e.target === clearBtn) return; fileInput.click(); });
dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
fileInput.addEventListener("change", () => setFile(fileInput.files[0]));
clearBtn.addEventListener("click", (e) => { e.stopPropagation(); clearFile(); });
["dragenter", "dragover"].forEach((ev) =>
  dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
["dragleave", "drop"].forEach((ev) =>
  dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
dz.addEventListener("drop", (e) => {
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (f) setFile(f);
});

// --- timer + eta -------------------------------------------------------------
function startTimer() {
  startedAt = Date.now();
  clearInterval(timer);
  timer = setInterval(() => { elapsedEl.textContent = mmss((Date.now() - startedAt) / 1000); }, 500);
}
function stopTimer() { clearInterval(timer); timer = null; }
function setDevice(note) {
  deviceEl.textContent = shortDevice(note);
  deviceEl.title = note;
  deviceEl.classList.toggle("cpu", /cpu/i.test(note));
}
function showEta(progress) {
  if (!progress || progress <= 0.02 || progress >= 1) { etaEl.textContent = ""; return; }
  const elapsed = (Date.now() - startedAt) / 1000;
  const remaining = elapsed * (1 - progress) / progress;
  etaEl.textContent = "~" + mmss(remaining) + " left";
}

// --- transcript rendering ----------------------------------------------------
function makeSegEl(seg) {
  const row = document.createElement("div");
  row.className = "seg";
  const t = document.createElement("span");
  t.className = "t";
  t.textContent = mmss(seg.start);
  t.title = "Jump to " + mmss(seg.start);
  const text = document.createElement("span");
  text.className = "seg-text";
  text.textContent = seg.text;
  row.appendChild(t);
  row.appendChild(text);

  t.addEventListener("click", (e) => { e.stopPropagation(); seekTo(seg.start); });
  row.addEventListener("click", () => { if (!editing) seekTo(seg.start); });
  text.addEventListener("input", () => { seg.text = text.textContent; });

  seg.el = row;
  seg.textEl = text;
  return row;
}
function appendSegment(ev) {
  const seg = { start: ev.start, end: ev.end, text: ev.text };
  segments.push(seg);
  transcriptEl.appendChild(makeSegEl(seg));
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

// --- media player + sync -----------------------------------------------------
function isVideoFile(f) {
  if (f.type && f.type.startsWith("video")) return true;
  if (f.type && f.type.startsWith("audio")) return false;
  return /\.(mp4|mov|mkv|webm|avi|m4v|mpg|mpeg|ogv|ts|3gp)$/i.test(f.name);
}
function buildPlayer(file) {
  if (mediaURL) { URL.revokeObjectURL(mediaURL); mediaURL = null; }
  playerWrap.innerHTML = "";
  mediaEl = null;
  if (!file) { playerWrap.hidden = true; return; }

  const el = document.createElement(isVideoFile(file) ? "video" : "audio");
  el.id = "player";
  el.controls = true;
  el.preload = "metadata";
  mediaURL = URL.createObjectURL(file);
  el.src = mediaURL;
  el.addEventListener("timeupdate", onTimeUpdate);
  el.addEventListener("error", () => {
    playerWrap.innerHTML =
      '<div class="player-note">Preview is not available for this file format in the browser. ' +
      "Transcription, editing, and export still work.</div>";
    mediaEl = null;
  });
  playerWrap.appendChild(el);
  playerWrap.hidden = false;
  mediaEl = el;
}
function seekTo(t) {
  if (!mediaEl) return;
  try { mediaEl.currentTime = Math.max(0, t); mediaEl.play().catch(() => {}); } catch (e) {}
}
function onTimeUpdate() {
  if (!mediaEl || !segments.length) return;
  const t = mediaEl.currentTime;
  let idx = -1;
  for (let i = 0; i < segments.length; i++) {
    if (segments[i].start <= t + 0.001) idx = i; else break;
  }
  if (idx === activeIdx) return;
  if (activeIdx >= 0 && segments[activeIdx]) segments[activeIdx].el.classList.remove("active");
  activeIdx = idx;
  if (idx >= 0 && segments[idx]) {
    segments[idx].el.classList.add("active");
    if (!mediaEl.paused) segments[idx].el.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

// --- search ------------------------------------------------------------------
function clearHighlights() {
  segments.forEach((s) => { if (s.textEl) s.textEl.textContent = s.text; });
  matches = []; matchIndex = -1;
}
function runSearch(q) {
  clearHighlights();
  q = (q || "").trim();
  if (!q) { searchCount.textContent = ""; searchPrev.hidden = searchNext.hidden = true; return; }
  const ql = q.toLowerCase();
  segments.forEach((s) => {
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

// --- edit --------------------------------------------------------------------
editToggle.addEventListener("click", () => {
  editing = !editing;
  editToggle.setAttribute("aria-pressed", editing ? "true" : "false");
  editToggle.textContent = editing ? "Done" : "Edit";
  transcriptEl.classList.toggle("editing", editing);
  if (editing && searchEl.value) { searchEl.value = ""; runSearch(""); }
  segments.forEach((s) => { if (s.textEl) s.textEl.contentEditable = editing ? "true" : "false"; });
});

// --- exports -----------------------------------------------------------------
function plainSegments() {
  return segments.map((s) => ({ start: s.start, end: s.end, text: (s.text || "").trim() }));
}
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
function exportBase() { return safeStem(currentFile ? currentFile.name : "transcript"); }
function totalDuration() { return segments.length ? segments[segments.length - 1].end : 0; }

async function exportFormat(fmt) {
  const segs = plainSegments();
  const base = exportBase();
  if (fmt === "txt") return downloadBlob(base + ".txt", buildTxt(segs));
  if (fmt === "srt") return downloadBlob(base + ".srt", buildSrt(segs), "text/plain;charset=utf-8");
  if (fmt === "vtt") return downloadBlob(base + ".vtt", buildVtt(segs), "text/vtt;charset=utf-8");
  if (fmt === "md") return downloadBlob(base + ".md", buildMd(segs, base), "text/markdown;charset=utf-8");
  if (fmt === "json") return downloadBlob(base + ".json", buildJson(segs, totalDuration(), base), "application/json");
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

// --- copy --------------------------------------------------------------------
function copyText(text, btn) {
  navigator.clipboard.writeText(text).then(() => {
    const old = btn.textContent;
    btn.textContent = "Copied";
    setTimeout(() => (btn.textContent = old), 1400);
  }).catch(() => toast("Copy failed", true));
}
copyBtn.addEventListener("click", () => copyText(buildTxt(plainSegments()), copyBtn));
copyTimesBtn.addEventListener("click", () => copyText(buildTextWithTimes(plainSegments()), copyTimesBtn));

// --- run / reset -------------------------------------------------------------
function resetForNewJob() {
  segments = []; activeIdx = -1; editing = false; matches = []; matchIndex = -1;
  transcriptEl.innerHTML = ""; transcriptEl.classList.remove("editing");
  editToggle.setAttribute("aria-pressed", "false"); editToggle.textContent = "Edit";
  searchEl.value = ""; searchCount.textContent = "";
  searchPrev.hidden = searchNext.hidden = true;
  if (mediaURL) { URL.revokeObjectURL(mediaURL); mediaURL = null; }
  playerWrap.innerHTML = ""; playerWrap.hidden = true; mediaEl = null;
  toolbar.hidden = true; folderEl.textContent = ""; rmeta.textContent = "";
}
function showUploadView() {
  resetForNewJob();
  resultEl.hidden = true;
  progPanel.hidden = true;
  uploadCard.hidden = false;
  newFileBtn.hidden = true;
}
newFileBtn.addEventListener("click", showUploadView);

go.addEventListener("click", () => {
  if (!currentFile) return;
  resetForNewJob();
  go.disabled = true; modelSel.disabled = true; langSel.disabled = true;
  uploadCard.hidden = true; newFileBtn.hidden = false;
  resultEl.hidden = false; progPanel.hidden = false;
  rtitle.textContent = "Transcript";
  fill.classList.add("indet"); fill.style.width = "";
  deviceEl.textContent = "preparing..."; deviceEl.classList.remove("cpu");
  statusEl.textContent = "Uploading..."; etaEl.textContent = "";
  startTimer();

  const fd = new FormData();
  fd.append("file", currentFile);
  fd.append("model", modelSel.value);
  fd.append("language", langSel.value);

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/jobs");
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) statusEl.textContent = "Uploading " + Math.round((e.loaded / e.total) * 100) + "%";
  };
  xhr.onload = () => {
    if (xhr.status !== 200) { fail(serverError(xhr)); return; }
    let data; try { data = JSON.parse(xhr.responseText); } catch (e) { fail("Bad server response."); return; }
    statusEl.textContent = "Queued...";
    streamEvents(data.job_id);
  };
  xhr.onerror = () => fail("Upload error (is the server running?).");
  xhr.send(fd);
});

function serverError(xhr) {
  try { const j = JSON.parse(xhr.responseText); if (j && j.detail) return j.detail; } catch (e) {}
  return "Upload failed: " + xhr.status + " " + (xhr.statusText || "");
}
function fail(msg) {
  stopTimer();
  fill.classList.remove("indet"); fill.style.width = "0";
  etaEl.textContent = "";
  statusEl.innerHTML = '<span class="err">' + escapeHtml(msg) + "</span>";
  go.disabled = false; modelSel.disabled = false; langSel.disabled = false;
}

function streamEvents(jobId) {
  const es = new EventSource("/api/jobs/" + jobId + "/events");
  es.onmessage = (e) => {
    let ev; try { ev = JSON.parse(e.data); } catch (err) { return; }
    if (ev.type === "status") {
      if (ev.device) setDevice(ev.device);
      statusEl.textContent = ev.message;
    } else if (ev.type === "language") {
      detectedLang = ev.language || "";
      statusEl.textContent = "Detected language: " + (ev.language || "?") +
        " (" + Math.round((ev.probability || 0) * 100) + "%)";
    } else if (ev.type === "segment") {
      const p = ev.progress || 0;
      fill.classList.remove("indet");
      fill.style.width = Math.round(p * 100) + "%";
      statusEl.textContent = "Transcribing " + Math.round(p * 100) + "%";
      showEta(p);
      appendSegment(ev);
    } else if (ev.type === "done") {
      finish(jobId, ev);
    } else if (ev.type === "error") {
      fail(ev.message || "Transcription failed.");
    }
  };
  es.addEventListener("end", () => {
    es.close();
    go.disabled = false; modelSel.disabled = false; langSel.disabled = false;
  });
  es.onerror = () => { /* keep partial transcript; the end event closes us normally */ };
}

function finish(jobId, ev) {
  stopTimer();
  fill.classList.remove("indet"); fill.style.width = "100%";
  etaEl.textContent = "";
  const took = mmss((Date.now() - startedAt) / 1000);
  const count = ev.segments ? ev.segments.length : segments.length;
  const dur = ev.duration || totalDuration();

  progPanel.hidden = true;
  rtitle.textContent = "Transcript: " + (currentFile ? currentFile.name : "audio");
  const bits = [count + " segment" + (count === 1 ? "" : "s"), mmss(dur) + " of audio", "done in " + took];
  if (detectedLang) bits.unshift(detectedLang.toUpperCase());
  rmeta.textContent = bits.join("  ·  ");

  buildPlayer(currentFile);
  toolbar.hidden = false;

  fetch("/api/jobs/" + jobId + "/folder").then((r) => r.json())
    .then((d) => { if (d && d.folder) folderEl.textContent = "Saved to: " + d.folder; })
    .catch(() => {});
}
