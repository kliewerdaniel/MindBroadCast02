// MindBroadcast console: SSE transcript + sequential audio playback.
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

let queue = [];        // segments waiting to play
let playing = false;
let stationRunning = false;

function setStationUI(running) {
  stationRunning = running;
  stationBtn.textContent = running ? "Stop the station" : "Start the station";
  stationDot.className = "dot " + (running ? "ok" : "unknown");
  stationText.textContent = running ? "station on air" : "station idle";
}

async function refreshHealth() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    const ok = data.ok;
    healthDot.className = "dot " + (ok ? "ok" : "bad");
    const s = data.services || {};
    healthText.textContent = ok
      ? "LIVE — llm ✓ tts ✓"
      : `degraded (llm ${s.llm ? "✓" : "✗"} tts ${s.tts ? "✓" : "✗"})`;
  } catch (e) {
    healthDot.className = "dot bad";
    healthText.textContent = "console unreachable";
  }
}

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

function playNext() {
  if (playing || queue.length === 0) return;
  const entry = queue.shift();
  playing = true;
  currentText.textContent = entry.text;
  currentMeta.textContent = entry.segment_id +
    (entry.duration_ms ? " · " + (entry.duration_ms / 1000).toFixed(1) + "s" : "");
  if (entry.audio_url) {
    player.src = entry.audio_url;
    player.play().catch(() => {});
  }
  player.onended = () => { playing = false; playNext(); };
}

function connect() {
  const es = new EventSource("/api/stream");
  es.onmessage = (ev) => {
    let data;
    try { data = JSON.parse(ev.data); } catch { return; }
    if (data.type === "segment") {
      addTranscript(data, false);
      queue.push(data);
      playNext();
    } else if (data.type === "station") {
      setStationUI(data.state === "on_air");
      if (data.state === "on_air") cycleInfo.textContent = "watching " + data.source + "…";
    } else if (data.type === "cycle") {
      // What the system observed and decided this cycle.
      const line = data.aired
        ? `watched ${data.articles} articles · ${data.claims} claims · ` +
          `${data.stories} stories → aired: ${data.story}`
        : `watched ${data.articles} articles · silent: ${data.silent_reason}`;
      cycleInfo.textContent = line;
    } else if (data.type === "done") {
      runState.textContent = `done — ${data.segments} segment(s)` +
        (data.errors && data.errors.length ? `, ${data.errors.length} error(s)` : "");
      demoBtn.disabled = false;
      liveBtn.disabled = false;
    } else if (data.type === "error") {
      runState.textContent = "error: " + data.message;
      demoBtn.disabled = false;
      liveBtn.disabled = false;
    }
  };
  es.onerror = () => { /* browser auto-reconnects */ };
}

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
    : { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ poll_seconds: 90 }) };
  try {
    const res = await fetch(url, opts);
    if (!stationRunning && res.ok) {
      setStationUI(true);
      runState.textContent = "station started";
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
  } catch (e) { /* ignore */ }
})();

refreshHealth();
setInterval(refreshHealth, 15000);
connect();
