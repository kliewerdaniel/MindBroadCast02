// MindBroadcast console: SSE transcript + sequential audio playback.
const player = document.getElementById("player");
const transcriptEl = document.getElementById("transcript");
const currentText = document.getElementById("current-text");
const currentMeta = document.getElementById("current-meta");
const healthDot = document.getElementById("health-dot");
const healthText = document.getElementById("health-text");
const demoBtn = document.getElementById("demo-btn");
const runState = document.getElementById("run-state");

let queue = [];        // segments waiting to play
let playing = false;

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
  const speaker = document.createElement("div");
  speaker.className = "speaker";
  speaker.textContent = entry.speaker || "presenter";
  const text = document.createElement("div");
  text.className = "text";
  text.textContent = entry.text;
  const meta = document.createElement("div");
  meta.className = "seg-meta";
  const secs = entry.duration_ms ? (entry.duration_ms / 1000).toFixed(1) + "s" : "";
  meta.textContent = [entry.segment_id, secs].filter(Boolean).join(" · ");
  li.append(speaker, text, meta);
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
    } else if (data.type === "done") {
      runState.textContent = `done — ${data.segments} segment(s)` +
        (data.errors && data.errors.length ? `, ${data.errors.length} error(s)` : "");
      demoBtn.disabled = false;
    } else if (data.type === "error") {
      runState.textContent = "error: " + data.message;
      demoBtn.disabled = false;
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
refreshHealth();
setInterval(refreshHealth, 15000);
connect();
