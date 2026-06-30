"use strict";

const $ = (id) => document.getElementById(id);
const dz = $("dropzone"), fileInput = $("file"), chip = $("filechip");
const fnEl = $("filename"), fsEl = $("filesize"), clearBtn = $("clearfile");
const go = $("go"), modelSel = $("model"), langSel = $("language");
const progPanel = $("progress"), deviceEl = $("device"), statusEl = $("status");
const elapsedEl = $("elapsed"), etaEl = $("eta"), fill = $("fill");
const devBadge = $("devbadge");
const resPanel = $("result"), rtitle = $("rtitle"), transcriptEl = $("transcript");
const folderEl = $("folder"), copyBtn = $("copy");
const dlTxt = $("dl-txt"), dlSrt = $("dl-srt"), dlVtt = $("dl-vtt");

let currentFile = null;
let timer = null, startedAt = 0;

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

// The device note can be long on CPU ("CPU (int8) -- GPU unavailable: ...").
// Show only the concise head ("CPU (int8)" / "GPU (CUDA, float16)") in the
// badge and keep the full reason in the hover tooltip.
function shortDevice(note) { return String(note).split(" -- ")[0].trim(); }

// Resolve GPU vs CPU once on load and show a resting badge in the header.
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

// Estimate time remaining from elapsed time and fraction done.
function showEta(progress) {
  if (!progress || progress <= 0.02 || progress >= 1) { etaEl.textContent = ""; return; }
  const elapsed = (Date.now() - startedAt) / 1000;
  const remaining = elapsed * (1 - progress) / progress;
  etaEl.textContent = "~" + mmss(remaining) + " left";
}

go.addEventListener("click", () => {
  if (!currentFile) return;
  go.disabled = true; modelSel.disabled = true; langSel.disabled = true;
  resPanel.hidden = true; transcriptEl.innerHTML = "";
  progPanel.hidden = false;
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
    if (e.lengthComputable) {
      const pct = Math.round((e.loaded / e.total) * 100);
      statusEl.textContent = "Uploading " + pct + "%";
    }
  };
  xhr.onload = () => {
    if (xhr.status !== 200) { fail(serverError(xhr)); return; }
    let data; try { data = JSON.parse(xhr.responseText); } catch { fail("Bad server response."); return; }
    statusEl.textContent = "Queued...";
    streamEvents(data.job_id);
  };
  xhr.onerror = () => fail("Upload error (is the server running?).");
  xhr.send(fd);
});

// Pull a clean message out of a FastAPI error response when we can.
function serverError(xhr) {
  try {
    const j = JSON.parse(xhr.responseText);
    if (j && j.detail) return j.detail;
  } catch { /* not JSON */ }
  return "Upload failed: " + xhr.status + " " + (xhr.statusText || "");
}

function fail(msg) {
  stopTimer();
  fill.classList.remove("indet"); fill.style.width = "0";
  etaEl.textContent = "";
  statusEl.innerHTML = '<span class="err">' + msg + "</span>";
  go.disabled = false; modelSel.disabled = false; langSel.disabled = false;
}

function streamEvents(jobId) {
  const es = new EventSource("/api/jobs/" + jobId + "/events");
  const segTexts = [];

  es.onmessage = (e) => {
    let ev; try { ev = JSON.parse(e.data); } catch { return; }
    if (ev.type === "status") {
      if (ev.device) setDevice(ev.device);
      statusEl.textContent = ev.message;
    } else if (ev.type === "language") {
      statusEl.textContent = "Detected language: " + (ev.language || "?") +
        " (" + Math.round((ev.probability || 0) * 100) + "%)";
    } else if (ev.type === "segment") {
      const p = ev.progress || 0;
      fill.classList.remove("indet");
      fill.style.width = Math.round(p * 100) + "%";
      statusEl.textContent = "Transcribing " + Math.round(p * 100) + "%";
      showEta(p);
      segTexts.push(ev.text);
      const span = document.createElement("span");
      span.className = "seg";
      span.innerHTML = '<span class="t">' + mmss(ev.start) + "</span>";
      span.appendChild(document.createTextNode(ev.text));
      transcriptEl.appendChild(span);
      transcriptEl.scrollTop = transcriptEl.scrollHeight;
    } else if (ev.type === "done") {
      stopTimer();
      fill.classList.remove("indet"); fill.style.width = "100%";
      etaEl.textContent = "";
      statusEl.textContent = "Done in " + mmss((Date.now() - startedAt) / 1000) +
        " (" + (ev.segments ? ev.segments.length : 0) + " segments, " +
        mmss(ev.duration || 0) + " of audio)";
      finish(jobId, currentFile ? currentFile.name : "transcript", segTexts.join("\n"));
    } else if (ev.type === "error") {
      fail(ev.message || "Transcription failed.");
    }
  };
  es.addEventListener("end", () => {
    es.close();
    go.disabled = false; modelSel.disabled = false; langSel.disabled = false;
  });
  es.onerror = () => { /* keep the partial transcript; end event closes us normally */ };
}

function finish(jobId, name, fullText) {
  rtitle.textContent = "Transcript: " + name;
  dlTxt.href = "/api/jobs/" + jobId + "/download/txt";
  dlSrt.href = "/api/jobs/" + jobId + "/download/srt";
  dlVtt.href = "/api/jobs/" + jobId + "/download/vtt";
  resPanel.hidden = false;
  fetch("/api/jobs/" + jobId + "/folder").then((r) => r.json())
    .then((d) => { folderEl.textContent = "Saved to: " + d.folder; }).catch(() => {});
  copyBtn.onclick = () => {
    navigator.clipboard.writeText(fullText).then(() => {
      copyBtn.textContent = "Copied"; setTimeout(() => (copyBtn.textContent = "Copy"), 1500);
    });
  };
}
