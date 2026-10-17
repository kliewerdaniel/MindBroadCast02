/* ═══════════════════════════════════════════════════════════════════
   MINDBROADCAST console — the signal instrument

   Three layers of visualization, all driven by real broadcast state:

   1. THE FIELD (canvas, background) — a flow of particles that carries the
      station's mental state. Dormant = slow, deep blue. Watching = brighter,
      faster. Composing = converging toward centre. Speaking = reacting to
      the actual audio spectrum. Each persona pulls the field toward its own
      hue, so the mind behind the single voice is visible.

   2. THE ORB — the broadcast's presence. Breathes while speaking; its colour
      is the current persona's signature.

   3. THE WAVEFORM — the real audio, via WebAudio AnalyserNode.

   Everything degrades gracefully: no AudioContext (or a blocked one) still
   gives a live field, just driven by synthetic pulses instead.
   ═══════════════════════════════════════════════════════════════════ */

const player = document.getElementById("player");
const transcriptEl = document.getElementById("transcript");
const currentText = document.getElementById("current-text");
const currentMeta = document.getElementById("current-meta");
const healthDot = document.getElementById("health-dot");
const healthText = document.getElementById("health-text");
const demoBtn = document.getElementById("demo-btn");
const liveBtn = document.getElementById("live-btn");
const stationBtn = document.getElementById("station-btn");
const stationDot = document.getElementById("station-dot");
const stationText = document.getElementById("station-text");
const cycleInfo = document.getElementById("cycle-info");
const runState = document.getElementById("run-state");
const runElapsed = document.getElementById("run-elapsed");
const activityPhase = document.getElementById("activity-phase");
const activityFill = document.getElementById("activity-fill");
const activityDetail = document.getElementById("activity-detail");
const activityLog = document.getElementById("activity-log");
const voiceDot = document.getElementById("voice-dot");
const voiceText = document.getElementById("voice-text");
const fieldMode = document.getElementById("field-mode");
const audioHint = document.getElementById("audio-hint");

const REGIONS = ["North America", "Europe", "Middle East", "Asia",
                 "Africa", "Latin America", "South Asia", "Oceania"];

let queue = [];            // segments waiting to play
let playing = false;
let stationRunning = false;
let audioEnabled = false;  // browsers block play() until a user gesture
let regionActivity = {};   // region -> 0..1 pulse

// ── persona signatures ───────────────────────────────────────────────
// The voice is one; the field carries the persona. Each has a hue that
// shapes how the particles move.
const PERSONAS = {
  analyst:   { hue: 165 },     // signal cyan
  skeptic:   { hue: 270 },     // violet
  historian: { hue: 32  },     // amber
  presenter: { hue: 165 },
};

// The field's mental state. Driven by real broadcast events.
const field = {
  mode: "dormant",       // dormant | watching | composing | speaking | silent
  persona: "presenter",
  pulse: 0,              // decaying impulse, bumped on each cycle/segment
  particles: [],
};

/* ── station UI ────────────────────────────────────────────────────── */

function setStationUI(running) {
  stationRunning = running;
  stationBtn.textContent = running ? "Stop the station" : "Start the station";
  stationDot.className = "dot " + (running ? "ok" : "unknown");
  stationText.textContent = running ? "on air" : "idle";
}

function setFieldMode(mode) {
  field.mode = mode;
  fieldMode.textContent = mode;
}

/* ── audio unlock (browsers require a gesture) ─────────────────────── */

function setAudioState(state) {
  audioEnabled = state === "ready";
  if (audioEnabled) {
    audioHint.textContent = "sound on";
    audioHint.classList.add("ready");
    runState.textContent = "audio on";
    initAudio();
    playNext();
  } else {
    runState.textContent = "click anywhere to enable sound";
  }
}

["click", "keydown", "touchstart"].forEach(evt =>
  document.addEventListener(evt, () => {
    if (!audioEnabled) setAudioState("ready");
  }, { once: false }));

/* ── segment playback ──────────────────────────────────────────────── */

function playNext() {
  if (playing || queue.length === 0) return;
  const entry = queue.shift();
  playing = true;
  currentText.textContent = entry.text;
  currentMeta.textContent = entry.segment_id +
    (entry.duration_ms ? " · " + (entry.duration_ms / 1000).toFixed(1) + "s" : "");
  markPlaying(entry.segment_id, true);
  const persona = entry.turns && entry.turns.length > 1 ? entry.turns[0].speaker
                                                        : "presenter";
  setSpeaking(true, persona);
  if (entry.audio_url) {
    player.src = entry.audio_url;
    const p = player.play();
    if (p && typeof p.catch === "function") {
      p.catch(() => {
        // Autoplay blocked: surface it instead of swallowing it silently, so
        // the listener knows why nothing is audible. A one-time gesture
        // (any click) unlocks playback for the rest of the session.
        playing = false;
        setSpeaking(false);
        markPlaying(entry.segment_id, false);
        setAudioState("blocked");
        queue.unshift(entry);
      });
    }
  }
  player.onended = () => {
    playing = false;
    setSpeaking(false);
    markPlaying(entry.segment_id, false);
    playNext();
  };
}

function setSpeaking(on, persona) {
  const stage = document.querySelector(".stage");
  if (on) {
    stage.classList.add("speaking");
    stage.dataset.persona = persona || "presenter";
    field.persona = persona || "presenter";
    setFieldMode("speaking");
    currentText.classList.add("speaking");
  } else {
    stage.classList.remove("speaking");
    currentText.classList.remove("speaking");
    delete stage.dataset.persona;
    field.persona = "presenter";
    setFieldMode(stationRunning ? "watching" : "dormant");
  }
}

/* ── transcript ────────────────────────────────────────────────────── */

function addTranscript(entry, highlight) {
  const li = document.createElement("li");
  if (highlight) li.className = "playing";
  const turns = entry.turns && entry.turns.length > 1 ? entry.turns : null;
  if (turns) {
    // Multi-voice segment: render each line attributed to its speaker so the
    // debate is legible, not one anonymous blob.
    li.className = (highlight ? "playing " : "") + "debate";
    const head = document.createElement("div");
    head.className = "speaker";
    head.textContent = entry.speaker || "panel";
    li.appendChild(head);
    for (const t of turns) {
      const line = document.createElement("div");
      line.className = "turn";
      const who = document.createElement("span");
      who.className = "turn-speaker";
      who.textContent = t.speaker || "presenter";
      const what = document.createElement("span");
      what.className = "turn-text";
      what.textContent = t.text;
      line.append(who, what);
      li.appendChild(line);
    }
  } else {
    const speaker = document.createElement("div");
    speaker.className = "speaker";
    speaker.textContent = entry.speaker || "presenter";
    const text = document.createElement("div");
    text.className = "text";
    text.textContent = entry.text;
    li.append(speaker, text);
  }
  const meta = document.createElement("div");
  meta.className = "seg-meta";
  const secs = entry.duration_ms ? (entry.duration_ms / 1000).toFixed(1) + "s" : "";
  meta.textContent = [entry.segment_id, secs].filter(Boolean).join(" · ");
  li.appendChild(meta);
  transcriptEl.appendChild(li);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
  return li;
}

function markPlaying(segmentId, on) {
  [...transcriptEl.children].forEach(li => {
    const meta = li.querySelector(".seg-meta");
    if (meta && meta.textContent.startsWith(segmentId)) {
      li.classList.toggle("playing", on);
    }
  });
}

/* ── health + region bars ──────────────────────────────────────────── */

async function refreshHealth() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    const ok = data.ok;
    healthDot.className = "dot " + (ok ? "ok" : "bad");
    const s = data.services || {};
    healthText.textContent = ok ? "llm ✓ tts ✓"
      : `degraded (llm ${s.llm ? "✓" : "✗"} tts ${s.tts ? "✓" : "✗"})`;

    // Which voice is actually speaking. The station's voice is Chris's,
    // cloned from chris.wav; when the clip or the cloning stack is missing it
    // silently speaks in a different voice. Say so instead of hiding it.
    const v = data.voice || {};
    if (v.cloned_voice) {
      voiceDot.className = "dot ok";
      voiceText.textContent = "voice: chris";
    } else if (v.clip_present) {
      voiceDot.className = "dot bad";
      voiceText.textContent = `voice: ${v.backend || "?"} (not cloned)`;
    } else {
      voiceDot.className = "dot bad";
      voiceText.textContent = `voice: ${v.backend || "none"} · chris.wav missing`;
    }
  } catch (e) {
    healthDot.className = "dot bad";
    healthText.textContent = "console unreachable";
    voiceDot.className = "dot unknown";
    voiceText.textContent = "voice: ?";
  }
}

function renderRegions() {
  const wrap = document.getElementById("regions");
  wrap.innerHTML = "";
  for (const r of REGIONS) {
    regionActivity[r] = 0;
    const row = document.createElement("div");
    row.className = "region-row";
    row.id = "region-" + r.replace(/\s+/g, "-").toLowerCase();
    const name = document.createElement("span");
    name.className = "region-name";
    name.textContent = r;
    const bar = document.createElement("div");
    bar.className = "region-bar";
    const fill = document.createElement("div");
    fill.className = "region-fill";
    bar.appendChild(fill);
    row.append(name, bar);
    wrap.appendChild(row);
  }
  document.getElementById("world-count").textContent = REGIONS.length;
}

function decayRegions() {
  for (const r of REGIONS) {
    if (regionActivity[r] > 0) {
      regionActivity[r] = Math.max(0, regionActivity[r] - 0.012);
      const fill = document.querySelector(
        "#region-" + r.replace(/\s+/g, "-").toLowerCase() + " .region-fill");
      if (fill) fill.style.width = (regionActivity[r] * 100).toFixed(1) + "%";
    }
  }
}

/* ═══════════════════════════════════════════════════════════════════
   THE FIELD — a flow of particles carrying the station's state
   ═══════════════════════════════════════════════════════════════════ */

const canvas = document.getElementById("field");
const ctx = canvas.getContext("2d");
let W = 0, H = 0, DPR = 1;

function resizeCanvas() {
  DPR = Math.min(window.devicePixelRatio || 1, 2);
  W = window.innerWidth;
  H = window.innerHeight;
  canvas.width = W * DPR;
  canvas.height = H * DPR;
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
}
window.addEventListener("resize", resizeCanvas);
resizeCanvas();

function seedParticles() {
  const count = Math.min(220, Math.floor((W * H) / 9000));
  field.particles = [];
  for (let i = 0; i < count; i++) {
    field.particles.push({
      x: Math.random() * W,
      y: Math.random() * H,
      vx: (Math.random() - 0.5) * 0.3,
      vy: (Math.random() - 0.5) * 0.3,
      r: Math.random() * 1.6 + 0.4,
      hue: 200 + Math.random() * 40,
      phase: Math.random() * Math.PI * 2,
    });
  }
}
seedParticles();
window.addEventListener("resize", seedParticles);

// Mode -> how the field behaves. Energy and pull toward the centre.
const MODE_TUNING = {
  dormant:   { energy: 0.06, pull: 0.0000, swirl: 0.0004, alpha: 0.35 },
  watching:  { energy: 0.16, pull: 0.0002, swirl: 0.0009, alpha: 0.55 },
  composing: { energy: 0.34, pull: 0.0018, swirl: 0.0022, alpha: 0.75 },
  speaking:  { energy: 0.55, pull: 0.0006, swirl: 0.0030, alpha: 0.9 },
  silent:    { energy: 0.08, pull: 0.0000, swirl: 0.0005, alpha: 0.4 },
};

let currentHue = 200;

function lerpHue(target, t) {
  const d = ((target - currentHue + 540) % 360) - 180;
  currentHue += d * t;
  return currentHue;
}

function drawField(t) {
  const tuning = MODE_TUNING[field.mode] || MODE_TUNING.dormant;
  // Persona hue drifts the whole field toward that persona's signature.
  const targetHue = (PERSONAS[field.persona] || PERSONAS.presenter).hue;
  const hue = lerpHue(targetHue, 0.03);

  // Fading trails rather than a hard clear — the liquid look.
  ctx.globalCompositeOperation = "source-over";
  ctx.fillStyle = "rgba(5, 6, 10, 0.22)";
  ctx.fillRect(0, 0, W, H);

  const cx = W / 2, cy = H / 2;
  const boost = 1 + field.pulse * 2.2;

  ctx.globalCompositeOperation = "lighter";
  for (const p of field.particles) {
    // Audio reactivity when speaking; synthetic breathing otherwise.
    let audioBoost;
    if (audioAnalyser && field.mode === "speaking") {
      audioBoost = audioLevel() * 3.2;
    } else {
      audioBoost = Math.sin(t * 0.0012 + p.phase) * 0.5 + 0.5;
    }

    const speed = (0.25 + tuning.energy * 2.4) * boost + audioBoost * 0.9;
    // Gentle pull to centre (composing = converge) and a swirl.
    const dx = cx - p.x, dy = cy - p.y;
    const dist = Math.sqrt(dx * dx + dy * dy) + 0.001;
    const nx = dx / dist, ny = dy / dist;

    p.vx += nx * tuning.pull * 60 + (-ny) * tuning.swirl * 60;
    p.vy += ny * tuning.pull * 60 + (nx) * tuning.swirl * 60;
    // Damping so it never accelerates without bound.
    p.vx *= 0.94;
    p.vy *= 0.94;

    p.x += p.vx * speed * 0.35;
    p.y += p.vy * speed * 0.35;

    // Wrap around the edges so the flow never empties.
    if (p.x < -20) p.x = W + 20;
    if (p.x > W + 20) p.x = -20;
    if (p.y < -20) p.y = H + 20;
    if (p.y > H + 20) p.y = -20;

    const twinkle = 0.5 + Math.sin(t * 0.003 + p.phase) * 0.5;
    const alpha = tuning.alpha * (0.35 + twinkle * 0.65);
    const radius = p.r * (1 + audioBoost * 0.5);

    const grd = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, radius * 6);
    grd.addColorStop(0, `hsla(${hue + p.hue * 0.08}, 90%, 72%, ${alpha})`);
    grd.addColorStop(1, `hsla(${hue + p.hue * 0.08}, 90%, 60%, 0)`);
    ctx.fillStyle = grd;
    ctx.beginPath();
    ctx.arc(p.x, p.y, radius * 6, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalCompositeOperation = "source-over";
}

/* ── waveform: the real audio spectrum ─────────────────────────────── */

const wfCanvas = document.getElementById("waveform");
const wfCtx = wfCanvas.getContext("2d");
let audioAnalyser = null;
let audioData = null;

function resizeWaveform() {
  const r = wfCanvas.getBoundingClientRect();
  wfCanvas.width = Math.max(1, r.width * DPR);
  wfCanvas.height = Math.max(1, r.height * DPR);
}
window.addEventListener("resize", resizeWaveform);
resizeWaveform();

function initAudio() {
  if (audioAnalyser) return;
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    const ac = new AC();
    const src = ac.createMediaElementSource(player);
    const an = ac.createAnalyser();
    an.fftSize = 256;
    src.connect(an);
    an.connect(ac.destination);
    audioAnalyser = an;
    audioData = new Uint8Array(an.frequencyBinCount);
  } catch (e) {
    audioAnalyser = null;   // degrade to synthetic motion
  }
}

function audioLevel() {
  if (!audioAnalyser || !audioData) return 0.4;
  audioAnalyser.getByteFrequencyData(audioData);
  let sum = 0;
  for (let i = 0; i < audioData.length; i += 4) sum += audioData[i];
  return sum / (audioData.length / 4) / 255;
}

function drawWaveform() {
  const w = wfCanvas.width / DPR, h = wfCanvas.height / DPR;
  wfCtx.clearRect(0, 0, w, h);
  const hue = currentHue;
  const bars = 48;

  if (audioAnalyser && audioData) {
    audioAnalyser.getByteFrequencyData(audioData);
  }
  for (let i = 0; i < bars; i++) {
    const v = (audioAnalyser && audioData)
      ? audioData[Math.floor(i / bars * audioData.length * 0.7)] / 255
      : (Math.sin(Date.now() * 0.002 + i * 0.4) * 0.5 + 0.5) *
        (field.mode === "speaking" ? 0.9 : 0.18);
    const bh = Math.max(2, v * h * 0.9);
    const x = (i / bars) * w;
    const bw = (w / bars) * 0.6;
    wfCtx.fillStyle = `hsla(${hue}, 90%, 65%, ${0.25 + v * 0.7})`;
    wfCtx.fillRect(x, h - bh, bw, bh);
  }
  wfCtx.fillStyle = `hsla(${hue}, 90%, 65%, 0.12)`;
  wfCtx.fillRect(0, h / 2, w, 0.5);
}

/* ── main loop ─────────────────────────────────────────────────────── */

function frame(t) {
  field.pulse *= 0.96;          // impulses decay
  drawField(t);
  drawWaveform();
  decayRegions();
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

/* ── SSE ───────────────────────────────────────────────────────────── */

function connect() {
  const es = new EventSource("/api/stream");
  es.onmessage = (ev) => {
    let data;
    try { data = JSON.parse(ev.data); } catch { return; }
    switch (data.type) {
      case "segment":
        addTranscript(data, false);
        queue.push(data);
        playNext();
        break;
      case "station":
        setStationUI(data.state === "on_air");
        if (data.state === "on_air") {
          setFieldMode("watching");
          field.pulse = 1;
          if (runStartedAt === null) markRunStart();
          pushActivity(`station on air${data.source ? " — " + data.source : ""}`);
        } else {
          setFieldMode("dormant");
          clearRunStart();
          pushActivity("station stopped");
        }
        break;
      case "cycle":
        onCycle(data);
        break;
      case "progress":
        onProgress(data);
        break;
      case "done":
        runState.textContent = `done — ${data.segments} segment(s)` +
          (data.errors && data.errors.length ? `, ${data.errors.length} error(s)` : "");
        demoBtn.disabled = false;
        liveBtn.disabled = false;
        break;
      case "error":
        runState.textContent = "error: " + data.message;
        demoBtn.disabled = false;
        liveBtn.disabled = false;
        break;
    }
  };
  es.onerror = () => { /* browser auto-reconnects */ };
}

function onProgress(data) {
  // The station reports each phase of its cycle. Show it as a live activity
  // line plus a short log, so the listener can always see that work is
  // happening rather than wondering whether it is hung.
  const phase = (data.phase || "working").toLowerCase();
  activityPhase.textContent = phase;
  activityPhase.classList.toggle("busy", phase !== "idle" && phase !== "waiting");

  if (data.total && data.current) {
    const pct = Math.min(100, Math.round((data.current / data.total) * 100));
    activityFill.classList.remove("indeterminate");
    activityFill.style.width = pct + "%";
  } else if (phase === "waiting") {
    // Countdown between cycles: show how far through the wait we are.
    const total = data.total || 1;
    const left = data.current || 0;
    const pct = Math.min(100, Math.max(0, Math.round(((total - left) / total) * 100)));
    activityFill.classList.remove("indeterminate");
    activityFill.style.width = pct + "%";
  } else {
    activityFill.classList.add("indeterminate");
  }

  let detail = data.detail || "";
  if (phase === "waiting") {
    detail = `next cycle in ${data.current}s`;
    // A countdown must not look frozen: don't log every tick.
    activityDetail.textContent = detail;
    return;
  }
  activityDetail.textContent = detail || "—";
  if (detail) pushActivity(`${phase} — ${detail}`);
}

function pushActivity(line) {
  if (!activityLog) return;
  const stamp = new Date().toLocaleTimeString([], { hour: "2-digit",
    minute: "2-digit", second: "2-digit" });
  const li = document.createElement("li");
  li.textContent = `${stamp}  ${line}`;
  activityLog.prepend(li);
  while (activityLog.children.length > 40) {
    activityLog.removeChild(activityLog.lastChild);
  }
}

/* Elapsed-time readout: proves the process is alive between events. */
let runStartedAt = null;
function markRunStart() { runStartedAt = Date.now(); }
function clearRunStart() { runStartedAt = null; runElapsed.textContent = ""; }
setInterval(() => {
  if (runStartedAt === null || !runElapsed) return;
  const secs = Math.floor((Date.now() - runStartedAt) / 1000);
  const m = Math.floor(secs / 60), s = secs % 60;
  runElapsed.textContent = `· running ${m}:${String(s).padStart(2, "0")}`;
}, 1000);

function onCycle(data) {
  document.getElementById("stat-articles").textContent = data.articles || 0;
  document.getElementById("stat-claims").textContent = data.claims || 0;
  document.getElementById("stat-stories").textContent = data.stories || 0;
  field.pulse = 1;
  if (field.mode !== "speaking") {
    setFieldMode(data.aired ? "composing" : "silent");
    setTimeout(() => {
      if (field.mode === "composing") {
        setFieldMode(stationRunning ? "watching" : "dormant");
      }
    }, 6000);
  }
  const line = data.aired
    ? `observed ${data.articles} · compiled ${data.claims} · ${data.stories} stories → ${data.story}`
    : `observed ${data.articles} · silent: ${data.silent_reason}`;
  cycleInfo.textContent = line;
}

/* ── controls ──────────────────────────────────────────────────────── */

async function broadcastDemo() {
  demoBtn.disabled = true;
  runState.textContent = "broadcasting…";
  const topics = [
    { objective: "Report the unemployment figure.",
      facts: ["Unemployment was 6.1% in March."],
      claims: ["c1"], evidence: ["e1"] },
    { objective: "Report the interest rate decision.",
      facts: ["The central bank raised interest rates by half a point on Tuesday."],
      claims: ["c2"], evidence: ["e2"] },
    { objective: "Report the rail investment.",
      facts: ["The transport ministry announced a two billion dollar rail investment."],
      claims: ["c3"], evidence: ["e3"] },
  ];
  try {
    const res = await fetch("/api/broadcast", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(topics),
    });
    if (res.status === 409) { runState.textContent = "already running"; demoBtn.disabled = false; }
    else if (!res.ok) { runState.textContent = "failed: " + res.status; demoBtn.disabled = false; }
  } catch (e) {
    runState.textContent = "request failed";
    demoBtn.disabled = false;
  }
}

demoBtn.addEventListener("click", broadcastDemo);

async function goLive() {
  liveBtn.disabled = true;
  demoBtn.disabled = true;
  runState.textContent = "watching the news…";
  cycleInfo.textContent = "";
  setFieldMode("composing");
  try {
    const res = await fetch("/api/broadcast/auto", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cycles: 1 }),
    });
    if (res.status === 409) { runState.textContent = "already running"; liveBtn.disabled = false; demoBtn.disabled = false; }
    else if (!res.ok) { runState.textContent = "failed: " + res.status; liveBtn.disabled = false; demoBtn.disabled = false; }
  } catch (e) {
    runState.textContent = "request failed";
    liveBtn.disabled = false;
    demoBtn.disabled = false;
  }
}
liveBtn.addEventListener("click", goLive);

async function toggleStation() {
  const url = stationRunning ? "/api/station/stop" : "/api/station/start";
  const opts = stationRunning
    ? { method: "POST" }
    : { method: "POST", headers: { "Content-Type": "application/json" } };
  if (!stationRunning) opts.body = JSON.stringify({ poll_seconds: 90, world: true });
  try {
    const res = await fetch(url, opts);
    if (!stationRunning && res.ok) {
      setStationUI(true);
      runState.textContent = "world station started";
      markRunStart();
      pushActivity("station started — watching the world");
      setFieldMode("watching");
      field.pulse = 1;
    } else if (stationRunning && res.ok) {
      runState.textContent = "stopping after current cycle…";
    } else {
      runState.textContent = "failed: " + res.status;
    }
  } catch (e) {
    runState.textContent = "request failed";
  }
}
stationBtn.addEventListener("click", toggleStation);

// Poll the station's phase so the UI shows progress even if an SSE event was
// missed (a reconnect, or a page loaded mid-cycle). This is the belt to SSE's
// braces: the listener should never have to guess whether work is happening.
setInterval(async () => {
  if (!stationRunning) return;
  try {
    const res = await fetch("/api/station");
    const data = await res.json();
    if (data.running === false) { setStationUI(false); clearRunStart(); return; }
    if (data.phase) {
      onProgress({ phase: data.phase, detail: data.detail,
                   current: data.current, total: data.total });
    }
    if (data.started_at) {
      const started = new Date(data.started_at).getTime();
      if (!Number.isNaN(started)) runStartedAt = started;
    }
    cycleInfo.textContent =
      `cycle ${data.cycles} · aired ${data.aired}` +
      (data.phase ? ` · ${data.phase}` : "");
  } catch (e) { /* ignore */ }
}, 3000);

// Reflect the server's station state on load.
(async function syncStation() {
  try {
    const res = await fetch("/api/station");
    const data = await res.json();
    setStationUI(!!data.running);
    setFieldMode(data.running ? "watching" : "dormant");
    if (data.running) {
      if (data.started_at) runStartedAt = new Date(data.started_at).getTime();
      if (data.phase) {
        onProgress({ phase: data.phase, detail: data.detail,
                     current: data.current, total: data.total });
      }
      pushActivity("station already running — reconnected");
    }
  } catch (e) { /* ignore */ }
})();

// Replay the transcript already on the server so a fresh page join is not empty.
(async function loadHistory() {
  try {
    const res = await fetch("/api/segments");
    const data = await res.json();
    for (const s of (data.segments || [])) addTranscript(s, false);
  } catch (e) { /* ignore */ }
})();

/* ── settings ──────────────────────────────────────────────────────── */

const settingsBtn = document.getElementById("settings-btn");
const settingsOverlay = document.getElementById("settings-overlay");
const settingsClose = document.getElementById("settings-close");
const settingsSave = document.getElementById("settings-save");
const settingsStatus = document.getElementById("settings-status");
const setPoll = document.getElementById("set-poll");
const setPollHint = document.getElementById("set-poll-hint");
const setWorld = document.getElementById("set-world");
const setPersona = document.getElementById("set-persona");
const setCompile = document.getElementById("set-compile");
const feedsList = document.getElementById("feeds-list");
const feedsCount = document.getElementById("feeds-count");
const feedsMsg = document.getElementById("feeds-msg");
const feedName = document.getElementById("feed-name");
const feedUrl = document.getElementById("feed-url");
const feedAddBtn = document.getElementById("feed-add-btn");

let minPollSeconds = 30;

function setSettingsStatus(text, kind) {
  settingsStatus.textContent = text || "";
  settingsStatus.className = "settings-status" + (kind ? " " + kind : "");
}

function setFeedsMsg(text, kind) {
  feedsMsg.textContent = text || "";
  feedsMsg.className = "feeds-msg" + (kind ? " " + kind : "");
}

async function openSettings() {
  settingsOverlay.hidden = false;
  setSettingsStatus("");
  setFeedsMsg("");
  await Promise.all([loadSettings(), loadFeeds()]);
}

function closeSettings() {
  settingsOverlay.hidden = true;
}

settingsBtn.addEventListener("click", openSettings);
settingsClose.addEventListener("click", closeSettings);
// Click the backdrop (not the panel) to dismiss; Escape closes too.
settingsOverlay.addEventListener("click", (e) => {
  if (e.target === settingsOverlay) closeSettings();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !settingsOverlay.hidden) closeSettings();
});

async function loadSettings() {
  try {
    const res = await fetch("/api/settings");
    const data = await res.json();
    const s = data.settings || {};
    minPollSeconds = data.min_poll_seconds || 30;
    setPoll.value = s.poll_seconds ?? 120;
    setPoll.min = minPollSeconds;
    setPollHint.textContent = `minimum ${minPollSeconds}s — faster polling ` +
      `does not produce more news, it just re-asks.`;
    setWorld.checked = !!s.use_world_feeds;
    setPersona.checked = !!s.multi_persona;
    setCompile.value = s.max_compile_per_cycle ?? 8;
  } catch (e) {
    setSettingsStatus("could not load settings", "bad");
  }
}

async function saveSettings() {
  setSettingsStatus("saving…");
  try {
    const res = await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        poll_seconds: Number(setPoll.value),
        use_world_feeds: setWorld.checked,
        multi_persona: setPersona.checked,
        max_compile_per_cycle: Number(setCompile.value),
      }),
    });
    if (!res.ok) {
      setSettingsStatus("save failed: " + res.status, "bad");
      return;
    }
    const data = await res.json();
    // Show what was ACTUALLY applied: values are clamped server-side, so the
    // field may differ from what was typed.
    const applied = data.settings || {};
    setPoll.value = applied.poll_seconds ?? setPoll.value;
    setCompile.value = applied.max_compile_per_cycle ?? setCompile.value;
    setSettingsStatus(
      `saved · ${applied.poll_seconds}s between cycles`, "good");
  } catch (e) {
    setSettingsStatus("save failed", "bad");
  }
}
settingsSave.addEventListener("click", saveSettings);

async function loadFeeds() {
  feedsList.innerHTML = "";
  try {
    const res = await fetch("/api/feeds");
    const data = await res.json();
    const feeds = data.feeds || [];
    const enabled = feeds.filter(f => f.enabled).length;
    feedsCount.textContent = `${enabled} of ${feeds.length} enabled`;
    if (!feeds.length) {
      feedsList.innerHTML =
        '<li class="feed-row"><div class="feed-main">' +
        '<span class="feed-name">No feeds yet</span>' +
        '<span class="feed-url">Add one below, or start the station to ' +
        'register the world feeds.</span></div></li>';
      return;
    }
    for (const f of feeds) feedsList.appendChild(feedRow(f));
  } catch (e) {
    setFeedsMsg("could not load feeds", "bad");
  }
}

function feedRow(f) {
  const li = document.createElement("li");
  li.className = "feed-row" + (f.enabled ? "" : " off");

  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.checked = !!f.enabled;
  toggle.title = f.enabled ? "Disable this feed" : "Enable this feed";
  toggle.addEventListener("change", async () => {
    try {
      const res = await fetch(`/api/feeds/${f.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: toggle.checked }),
      });
      if (!res.ok) { toggle.checked = !toggle.checked; setFeedsMsg("could not update feed", "bad"); return; }
      li.classList.toggle("off", !toggle.checked);
      setFeedsMsg(`${f.name} ${toggle.checked ? "enabled" : "disabled"}`, "good");
      loadFeeds();
    } catch (e) {
      toggle.checked = !toggle.checked;
      setFeedsMsg("could not update feed", "bad");
    }
  });

  const main = document.createElement("div");
  main.className = "feed-main";
  const name = document.createElement("span");
  name.className = "feed-name";
  name.textContent = f.name;
  if (f.curated) {
    const badge = document.createElement("span");
    badge.className = "feed-badge";
    badge.textContent = "world";
    name.appendChild(badge);
  }
  if (f.failure_count > 0) {
    const badge = document.createElement("span");
    badge.className = "feed-badge warn";
    badge.textContent = `${f.failure_count} fail`;
    badge.title = "consecutive failed fetches";
    name.appendChild(badge);
  }
  const url = document.createElement("span");
  url.className = "feed-url";
  url.textContent = f.endpoint;
  url.title = f.endpoint;
  main.append(name, url);

  const actions = document.createElement("div");
  actions.className = "feed-actions";
  const editBtn = document.createElement("button");
  editBtn.className = "ghost";
  editBtn.textContent = "Edit";
  editBtn.addEventListener("click", () => editFeed(f));
  const delBtn = document.createElement("button");
  delBtn.className = "ghost";
  delBtn.textContent = "Remove";
  delBtn.addEventListener("click", () => removeFeed(f));
  actions.append(editBtn, delBtn);

  li.append(toggle, main, actions);
  return li;
}

async function editFeed(f) {
  const newName = window.prompt(`Name for this feed:`, f.name);
  if (newName === null) return;
  const newUrl = window.prompt(`Feed URL:`, f.endpoint);
  if (newUrl === null) return;
  try {
    const res = await fetch(`/api/feeds/${f.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newName, endpoint: newUrl }),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      setFeedsMsg(detail.detail || "could not edit feed", "bad");
      return;
    }
    setFeedsMsg("feed updated — takes effect next cycle", "good");
    loadFeeds();
  } catch (e) {
    setFeedsMsg("could not edit feed", "bad");
  }
}

async function removeFeed(f) {
  if (!window.confirm(`Remove "${f.name}"?`)) return;
  try {
    const res = await fetch(`/api/feeds/${f.id}`, { method: "DELETE" });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      setFeedsMsg(detail.detail || "could not remove feed", "bad");
      return;
    }
    setFeedsMsg("feed removed", "good");
    loadFeeds();
  } catch (e) {
    setFeedsMsg("could not remove feed", "bad");
  }
}

feedAddBtn.addEventListener("click", async () => {
  const url = feedUrl.value.trim();
  if (!url) { setFeedsMsg("a feed URL is required", "bad"); return; }
  setFeedsMsg("adding…");
  try {
    const res = await fetch("/api/feeds", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: feedName.value.trim(), endpoint: url }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { setFeedsMsg(data.detail || "could not add feed", "bad"); return; }
    setFeedsMsg(data.existed
      ? "that feed was already registered"
      : `added ${data.name}`, "good");
    feedName.value = ""; feedUrl.value = "";
    loadFeeds();
  } catch (e) {
    setFeedsMsg("could not add feed", "bad");
  }
});

renderRegions();
refreshHealth();
setInterval(refreshHealth, 15000);
connect();
