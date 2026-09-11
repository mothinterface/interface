"use strict";
const $ = id => document.getElementById(id);
let token = "", busy = false, lastSelection = 0;
async function api(path, data) {
  const response = await fetch(`/api/${path}`, {
    method: data === undefined ? "GET" : "POST",
    headers: {Authorization: `Bearer ${token}`, "Content-Type": "application/json"},
    body: data === undefined ? undefined : JSON.stringify(data),
    signal: AbortSignal.timeout(2000)
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Connection failed");
  return body;
}
async function command(path, data = {}) {
  try { await api(path, data); $("error").textContent = ""; await refresh(); }
  catch (error) { $("error").textContent = error.message; }
}
$("connect").onclick = async () => { token = $("token").value.trim(); await refresh(); };
$("arm").onclick = () => command("arm");
$("stop").onclick = () => command("stop");
$("choose").onclick = () => command("mode", {mode: "choose"});
$("click").onclick = () => command("mode", {mode: "click"});
document.addEventListener("keydown", event => { if (event.key === "Escape" && token) command("stop"); });
function render(state) {
  $("output").textContent = state.desktop ? "Real desktop control" : "Demo · no desktop input";
  $("choose").setAttribute("aria-pressed", state.mode === "choose");
  $("click").setAttribute("aria-pressed", state.mode === "click");
  $("arm").disabled = !state.connected || !state.sample.ready || state.armed;
  $("arm").textContent = state.armed ? `Armed · ${Math.ceil(state.seconds_left)}s` : "Arm control";
  $("status").textContent = !state.connected ? "Signal source disconnected · control stopped" :
    !state.sample.ready ? "Calibrating the quiet baseline…" :
    state.armed ? `Listening · ${state.sample.source} · ${state.mode === "choose" ? "strong → 1 / medium → 2 / low → 3" : "strong → click"}` :
    "Ready · select the mode and arm to accept signals";
  const options = state.mode === "click" ? [{label: "Click at cursor", action: "click"}] : state.options;
  const strengths = ["STRONG", "MEDIUM", "LOW"];
  const bands = [`≥ ${state.strong.toFixed(3)} V`, `${state.medium.toFixed(3)}–${state.strong.toFixed(3)} V`, `Detection threshold–${state.medium.toFixed(3)} V`];
  const latest = state.history[0];
  const cards = options.map((option, i) => {
    const card = document.createElement("article"); card.className = "option";
    if (latest && latest.time * 1000 > Date.now() - 1500 && (state.mode === "click" || latest.option === i + 1)) card.classList.add("selected");
    const number = document.createElement("span"); number.className = "number"; number.textContent = `${strengths[i]} SIGNAL`;
    const title = document.createElement("strong"); title.textContent = option.label;
    const band = document.createElement("p"); band.textContent = bands[i];
    const action = document.createElement("p"); action.textContent = `Action: ${option.action}`;
    card.append(number, title, band, action); return card;
  });
  $("options").replaceChildren(...cards);
  if (latest && latest.time !== lastSelection) {
    lastSelection = latest.time;
    $("history").replaceChildren(...state.history.map(entry => {
      const li = document.createElement("li");
      li.textContent = `${new Date(entry.time * 1000).toLocaleTimeString()} · ${entry.label} · ${entry.strength} (${entry.peak.toFixed(4)} V) · ${entry.result}`;
      return li;
    }));
  }
  if (state.sample.ready && state.sample.threshold >= state.medium) {
    $("error").textContent = "Noise threshold is at or above the medium band. Improve the signal or recalibrate the strength thresholds.";
  }
  const s = state.sample;
  $("readout").textContent = s.voltage === undefined ? "Waiting" : `Input ${s.voltage.toFixed(4)} V · threshold ${s.threshold.toFixed(4)} V`;
  const canvas = $("chart"), ctx = canvas.getContext("2d"), trace = state.trace;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const max = Math.max(state.strong * 1.3, ...trace.map(p => Math.max(p.envelope, p.threshold))) || 1;
  for (const [field, color] of [["threshold", "#e1ba78"], ["envelope", "#c3ffa4"]]) {
    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath();
    trace.forEach((p, i) => { const x = i / Math.max(1, trace.length - 1) * canvas.width, y = canvas.height - 12 - p[field] / max * (canvas.height - 24); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); });
    ctx.stroke();
  }
}
async function refresh() {
  if (!token || busy) return;
  busy = true;
  try { const state = await api("state"); $("error").textContent = ""; render(state); }
  catch (error) { $("error").textContent = error.message; $("status").textContent = "Controller connection unavailable"; $("arm").disabled = true; }
  finally { busy = false; }
}
setInterval(refresh, 150);
