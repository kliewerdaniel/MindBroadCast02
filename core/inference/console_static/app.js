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
  } catch (e) {
    healthDot.className = "dot bad";
    healthText.textContent = "console unreachable";
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
        } else {
          setFieldMode("dormant");
        }
        break;
      case "cycle":
        onCycle(data);
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

// Reflect the server's station state on load.
(async function syncStation() {
  try {
    const res = await fetch("/api/station");
    const data = await res.json();
    setStationUI(!!data.running);
    setFieldMode(data.running ? "watching" : "dormant");
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

renderRegions();
refreshHealth();
setInterval(refreshHealth, 15000);
connect();
