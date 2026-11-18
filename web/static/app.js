/* Clipper web app: paste a link -> live progress -> review -> render; library with copy + upload checkmarks.
   Visual + motion language: Apple-style (see .claude/skills/apple-design). */
"use strict";

// ------------------------------------------------------------------ helpers
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const app = $("#app");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const SPRING = () => getComputedStyle(document.documentElement).getPropertyValue("--spring").trim() || "ease-out";

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = data.error || (Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(", ") : data.detail) || res.statusText;
    throw new Error(msg);
  }
  return data;
}

// SF-Symbols-style line icons
const P = {
  download: '<path d="M12 4v11m0 0l-4.5-4.5M12 15l4.5-4.5"/><path d="M5 19.5h14"/>',
  wave: '<path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2"/>',
  chart: '<path d="M4 19.5h16"/><path d="M5 16l4.5-5 3.5 3 6-7.5"/>',
  sparkles: '<path d="M10 3.5l1.6 4.4L16 9.5l-4.4 1.6L10 15.5l-1.6-4.4L4 9.5l4.4-1.6z"/><path d="M17.5 14l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
  film: '<rect x="6.5" y="2.5" width="11" height="19" rx="2.5"/><path d="M10.5 9.3l3.6 2.7-3.6 2.7z"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  chevron: '<path d="M9.5 6l6 6-6 6"/>',
  copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2.5"/><path d="M15.5 8.5V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7.5a2 2 0 0 0 2 2h2.5"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.4-4.4"/>',
  folder: '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
  scissors: '<circle cx="6.5" cy="7" r="2.5"/><circle cx="6.5" cy="17" r="2.5"/><path d="M8.7 8.3L20 17M8.7 15.7L20 7"/>',
  pencil: '<path d="M15.5 4.5l4 4L9 19H5v-4z"/><path d="M13.5 6.5l4 4"/>',
  book: '<path d="M4.5 5.5c2.5-1 5-1 7.5.5 2.5-1.5 5-1.5 7.5-.5v13c-2.5-1-5-1-7.5.5-2.5-1.5-5-1.5-7.5-.5z"/><path d="M12 6v13"/>',
  seal: '<path d="M12 3l2.2 1.6 2.7-.2.9 2.6 2.3 1.4-.8 2.6.8 2.6-2.3 1.4-.9 2.6-2.7-.2L12 21l-2.2-1.6-2.7.2-.9-2.6-2.3-1.4.8-2.6-.8-2.6 2.3-1.4.9-2.6 2.7.2z"/><path d="M8.8 12.2l2.2 2.2 4.3-4.6"/>',
  quote: '<path d="M4.5 6.5a2 2 0 0 1 2-2h11a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H11l-4.5 3.5v-3.5h0a2 2 0 0 1-2-2z"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/>',
  warn: '<path d="M12 4l9 15.5H3z"/><path d="M12 10v4M12 17h.01"/>',
  party: '<circle cx="12" cy="12" r="8.5"/><path d="M8 12.5l2.7 2.7L16.5 9.5"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  terminal: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M7.5 9.5l3 2.5-3 2.5M12.5 15h4"/>',
  card: '<rect x="3.5" y="5.5" width="17" height="13" rx="2.5"/><path d="M3.5 10h17"/>',
  slider: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  resume: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4.5v4h-4"/>',
  grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
  twitch: '<path d="M5 4h14v10l-4 4h-3.5L9 20.5V18H5z"/><path d="M11 8.5v4M15 8.5v4"/>',
  youtube: '<rect x="3" y="6" width="18" height="12" rx="4"/><path d="M10.5 9.5l4 2.5-4 2.5z"/>',
  kick: '<path d="M6.5 4.5v15M17 4.5L9.5 12l7.5 7.5"/>',
  file: '<path d="M7 3.5h7l4 4v13H7z"/><path d="M14 3.5v4h4"/>',
};
const icon = (name, cls = "i") => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${P[name] || ""}</svg>`;
const PLAY = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7.5 4.8v14.4L19 12z"/></svg>';

function toast(text, err = false) {
  const el = document.createElement("div");
  el.className = "toast" + (err ? " err" : "");
  el.innerHTML = `${icon(err ? "warn" : "party")}<span>${esc(text)}</span>`;
  $("#toasts").append(el);
  requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add("in")));
  setTimeout(() => el.classList.add("in"), 80);
  setTimeout(() => {
    el.classList.remove("in");       // exits along the path it came in on
    el.addEventListener("transitionend", () => el.remove(), { once: true });
    setTimeout(() => el.remove(), 800);
  }, err ? 6000 : 2400);
}

const fmtTs = (sec) => {
  sec = Math.max(0, Math.round(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
};
const fmtDur = (sec) => {
  sec = Math.max(0, Math.round(sec || 0));
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60), s = sec % 60;
  if (m < 60) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
};
const timeAgo = (t) => {
  const d = Date.now() / 1000 - t;
  if (d < 45) return "Just now";
  if (d < 3600) return `${Math.round(d / 60)} min ago`;
  if (d < 86400) return `${Math.round(d / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

const PLATFORMS = { twitch: "Twitch", youtube: "YouTube", kick: "Kick", local: "Local file" };
const platformName = (p) => PLATFORMS[p] || (p ? p[0].toUpperCase() + p.slice(1) : "Video");
const detectPlatform = (url) =>
  /twitch\.tv/i.test(url) ? "twitch" : /(youtube\.com|youtu\.be)/i.test(url) ? "youtube" : /kick\.com/i.test(url) ? "kick" : "";
const STATUS_LABEL = {
  queued: "Queued", running: "In progress", review: "Ready for review", queued_render: "Render queued",
  rendering: "Rendering", done: "Done", error: "Failed", cancelled: "Cancelled", interrupted: "Interrupted",
};
const ACTIVE = ["queued", "running", "queued_render", "rendering"];
const STAGES = [
  ["download", "Audio", "download"], ["transcribe", "Transcript", "wave"], ["score", "Moments", "chart"],
  ["pick", "Claude", "sparkles"], ["review", "Review", "eye"], ["render", "Render", "film"],
];
const STAGE_TITLE = { download: "Fetching the audio", transcribe: "Transcribing", score: "Finding the moments",
  pick: "Claude is picking", review: "Your review", render: "Rendering" };
const FEED = { read: ["book", "blue"], cut: ["scissors", "orange"], look: ["eye", "purple"], write: ["pencil", "indigo"],
  check: ["seal", "green"], claude: ["quote", "gray"], done: ["party", "green"], download: ["download", "blue"],
  transcribe: ["wave", "teal"], score: ["chart", "pink"], pick: ["sparkles", "indigo"], info: ["info", "gray"] };

const hookHtml = (hook) => esc(hook).replace(/\*(.+?)\*/g, "<mark>$1</mark>");
const scoreBadge = (s, reason) => s == null ? ""
  : `<span class="score ${s >= 80 ? "hi" : s >= 60 ? "mid" : ""}" title="${esc(reason ? "Virality score · " + reason : "Virality score")}">${Math.round(s)}</span>`;
const capsule = (p) => p ? `<span class="capsule ${esc(p)}">${esc(platformName(p))}</span>` : "";
const statusCapsule = (s) => `<span class="capsule st-${esc(s)}"><span class="dot"></span>${esc(STATUS_LABEL[s] || s)}</span>`;

function notify(title, body) {
  if (!("Notification" in window) || Notification.permission !== "granted" || !document.hidden) return;
  try { new Notification(title, { body }); } catch (e) { /* ignore */ }
}

async function copyText(text, btn) {
  try { await navigator.clipboard.writeText(text); }
  catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text; document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove();
  }
  if (!btn) return;
  btn.classList.add("ok");
  btn.innerHTML = icon("check");
  btn.setAttribute("aria-label", "Copied");
  clearTimeout(btn._t);
  btn._t = setTimeout(() => { btn.classList.remove("ok"); btn.innerHTML = icon("copy"); btn.setAttribute("aria-label", "Copy"); }, 1600);
}

/** Show a freshly rendered view: it materializes (opacity + slight lift) with a spring. */
function mount(html) {
  app.innerHTML = `<div class="view">${html}</div>`;
  const v = app.firstElementChild;
  const show = () => v.classList.add("in");
  requestAnimationFrame(() => requestAnimationFrame(show));
  setTimeout(show, 80);   // background tabs throttle animation frames: never leave the page invisible
  return v;
}

// nav: scroll-edge effect only while content is underneath
addEventListener("scroll", () => $("#nav").classList.toggle("scrolled", scrollY > 4), { passive: true });

// ------------------------------------------------------------------ top bar: active run indicator
let overviewCache = null;
async function refreshOverview() {
  try { overviewCache = await api("GET", "/api/overview"); } catch (e) { return null; }
  const active = overviewCache.runs.find((r) => ACTIVE.includes(r.status) || r.status === "review");
  const el = $("#activeRun");
  if (active) {
    el.hidden = false;
    el.href = `#/run/${active.id}`;
    const color = active.status === "review" ? "var(--orange)" : "var(--accent)";
    el.innerHTML = `<span class="dot" style="color:${color};margin-left:.35rem"></span>${esc(STATUS_LABEL[active.status])} · ${esc(active.title || active.url)}`;
  } else {
    el.hidden = true;
  }
  return overviewCache;
}
setInterval(() => { if (!document.hidden) refreshOverview(); }, 8000);

// ------------------------------------------------------------------ router
let cleanup = null;
function route() {
  if (cleanup) { cleanup(); cleanup = null; }
  const h = location.hash || "#/new";
  const page = h.startsWith("#/library") ? "library" : h.startsWith("#/settings") ? "settings" : "new";
  $$("[data-nav]").forEach((a) => a.classList.toggle("on", a.dataset.nav === page));
  scrollTo(0, 0);
  if (h.startsWith("#/run/")) cleanup = viewRun(parseInt(h.split("/")[2], 10));
  else if (page === "library") cleanup = viewLibrary();
  else if (page === "settings") viewSettings();
  else viewNew();
  refreshOverview();
}
addEventListener("hashchange", route);
addEventListener("DOMContentLoaded", route);

// ------------------------------------------------------------------ page: new clips
async function viewNew() {
  document.title = "New Clips · Clipper";
  mount(`
    <section class="hero">
      <h1 class="t-hero">Turn any stream into <span class="grad">clips.</span></h1>
      <p>Paste a Twitch, YouTube or Kick link. Claude finds the moments worth watching, you choose, and only those seconds are downloaded and rendered.</p>
      <form class="paste" id="pasteForm" autocomplete="off">
        <span class="plat" id="plat" aria-hidden="true">${icon("link")}</span>
        <input id="url" name="url" placeholder="Paste a video link" aria-label="Video link" required autofocus>
        <button class="btn primary" id="go" type="submit">Clip It</button>
      </form>
      <div class="works">Twitch VODs · YouTube · Kick VODs · or a video file path</div>
      <details class="disclosure" id="opts">
        <summary>Options ${icon("chevron")}</summary>
        <div class="surface options">
          <div>
            <label class="label" for="extra">Instructions for Claude</label>
            <textarea class="field-input" id="extra" placeholder="Only the funniest moments. Titles in German."></textarea>
          </div>
          <div>
            <label class="label" for="link">Link in every description</label>
            <input class="field-input" id="link" placeholder="https://linktr.ee/you">
            <div class="hint" id="linkHint"></div>
          </div>
        </div>
      </details>
      <div class="form-msg" id="formMsg"></div>
    </section>
    <div class="section-head"><h2 class="t-title2">Recent</h2><a href="#/library">Library ${icon("chevron")}</a></div>
    <div class="list" id="runs"><div class="empty">Loading…</div></div>`);

  const ov = await refreshOverview();
  const defLink = ov ? ov.settings.desc_link : "";
  const linkIn = $("#link");
  linkIn.value = defLink;
  const linkHint = () => {
    $("#linkHint").textContent = !defLink
      ? "No default link yet (Settings). Leave empty for none."
      : linkIn.value.trim() === defLink ? "Your default link. Change or clear it for this video only." : "Only for this video.";
  };
  linkHint();
  linkIn.addEventListener("input", linkHint);
  const urlIn = $("#url");
  urlIn.addEventListener("input", () => {
    const p = detectPlatform(urlIn.value);
    const el = $("#plat");
    el.className = "plat " + p;
    el.innerHTML = icon(p || "link");
  });
  if (ov) {
    const msgs = [];
    if (!ov.claude_found && !ov.fake_picker)
      msgs.push(`<div class="banner warn">${icon("warn")}<span><b>Claude Code isn't installed.</b> The picking step needs it: run <span class="mono">npm install -g @anthropic-ai/claude-code</span>, then <span class="mono">claude</span> and type <span class="mono">/login</span>.</span></div>`);
    if (ov.fake_picker) msgs.push(`<div class="banner info">${icon("info")}<span>Test mode: a fake picker stands in for Claude.</span></div>`);
    $("#formMsg").innerHTML = msgs.join("");
    renderRunsList(ov.runs);
  }
  $("#pasteForm").addEventListener("submit", (e) => { e.preventDefault(); submitRun(false); });
}

function renderRunsList(runs) {
  const box = $("#runs");
  if (!box) return;
  box.innerHTML = runs.length
    ? runs.slice(0, 10).map((r) => `<a class="row" href="#/run/${r.id}">
        <span class="tile ${esc(r.platform || "gray")}">${icon(["twitch", "youtube", "kick"].includes(r.platform) ? r.platform : "file")}</span>
        <span style="min-width:0"><div class="t">${esc(r.title || r.url)}</div>
          <div class="m">${esc([r.channel, timeAgo(r.created_at)].filter(Boolean).join(" · "))}</div></span>
        ${statusCapsule(r.status)}<span class="chev">${icon("chevron")}</span></a>`).join("")
    : `<div class="empty">Clips you make show up here.</div>`;
}

async function submitRun(force) {
  const btn = $("#go");
  const url = $("#url").value.trim();
  if (!url) return;
  const defLink = overviewCache ? overviewCache.settings.desc_link : "";
  const linkVal = $("#link").value.trim();
  const body = { url, extra_prompt: $("#extra").value, desc_link: linkVal === defLink ? null : linkVal, force };
  btn.disabled = true;
  btn.textContent = "Checking…";
  $("#formMsg").innerHTML = "";
  if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
  try {
    const res = await api("POST", "/api/runs", body);
    if (res.run || res.attach) {
      if (res.attach) toast("Already in progress");
      location.hash = `#/run/${res.run || res.attach}`;
      return;
    }
    if (res.duplicate) {
      const d = res.duplicate;
      $("#formMsg").innerHTML = `<div class="banner info dup">${icon("info")}<span><b>Already clipped.</b> ${esc(d.title)} by ${esc(d.channel)} has ${d.clips} clip${d.clips === 1 ? "" : "s"}.
        Clipping it again replaces them in the library; the old files move to the job's archive folder.</span>
        <a class="btn small" href="#/library" id="dupOpen">Open Library</a>
        <button class="btn small plain" type="button" id="dupAgain">Clip Again</button></div>`;
      $("#dupOpen").addEventListener("click", () => sessionStorage.setItem("openVideo", d.video_pk));
      $("#dupAgain").addEventListener("click", () => submitRun(true));
    }
  } catch (e) {
    $("#formMsg").innerHTML = `<div class="banner err">${icon("warn")}<span>${esc(e.message)}</span></div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Clip It";
  }
}

// ------------------------------------------------------------------ page: run
function viewRun(id) {
  let st = null, heat = null, heatLoading = false, lastStatus = null;
  const selection = new Set();
  let selectionInit = false;

  mount(`<div class="run-head"><div><div class="meta" id="rmeta"></div><h1 class="t-large" id="rtitle">Loading…</h1></div>
      <div class="run-actions" id="ractions"></div></div>
    <div id="rerror"></div>
    <div class="surface stepper" id="rail"></div>
    <div class="mc">
      <div class="stack">
        <section class="surface now" id="now"></section>
        <section class="surface card-pad"><div class="card-head"><h2 class="t-headline">The stream at a glance</h2><span class="t-foot tertiary" id="heatLegend"></span></div>
          <div class="heat" id="heat"><div class="heat-empty">Appears once the moments are found</div></div></section>
      </div>
      <section class="surface card-pad"><div class="card-head"><h2 class="t-headline">Activity</h2><span class="t-foot tertiary" id="feedCount"></span></div><ul class="feed" id="feed"></ul></section>
    </div>
    <div id="clipsSec"></div>
    <details class="log"><summary>Show technical log</summary><pre id="logPre"></pre></details>`);

  const es = new EventSource(`/api/runs/${id}/events`);
  es.onmessage = (ev) => { st = JSON.parse(ev.data); update(); };
  es.onerror = () => { if (st && !ACTIVE.includes(st.status)) es.close(); };
  const tick = setInterval(() => { if (st) { renderRail(); renderNow(); } }, 1000);

  const eta = (sd) => {
    if (sd.state !== "active" || !sd.started) return "";
    const el = Date.now() / 1000 - sd.started;
    return sd.pct >= 3 && sd.pct < 100 ? fmtDur((el * (100 - sd.pct)) / sd.pct) : "";
  };

  function renderHead() {
    const v = st.video || {};
    $("#rtitle").textContent = v.title || st.url;
    $("#rmeta").innerHTML = [capsule(v.platform), v.channel ? `<span>${esc(v.channel)}</span>` : "",
      v.duration ? `<span>${fmtTs(v.duration)}</span>` : "", statusCapsule(st.status)].filter(Boolean).join("<span class='tertiary'>·</span>");
    const acts = [];
    if (ACTIVE.includes(st.status)) acts.push(`<button class="btn" id="bCancel">${icon("x")} Cancel</button>`);
    if (["error", "interrupted", "cancelled"].includes(st.status)) acts.push(`<button class="btn primary" id="bResume">${icon("resume")} Resume</button>`);
    if (st.status === "done") acts.push(`<a class="btn primary" href="#/library">Open Library</a>`);
    $("#ractions").innerHTML = acts.join("");
    const c = $("#bCancel"), r = $("#bResume");
    if (c) c.onclick = async () => { if (confirm("Stop this run?")) await api("POST", `/api/runs/${id}/cancel`).catch((e) => toast(e.message, true)); };
    if (r) r.onclick = async () => { await api("POST", `/api/runs/${id}/resume`).catch((e) => toast(e.message, true)); };
    $("#rerror").innerHTML = (st.error ? `<div class="banner err">${icon("warn")}<span>${esc(st.error)}</span></div>` : "")
      + (st.notes || []).map((n) => `<div class="banner warn">${icon("info")}<span>${esc(n)}</span></div>`).join("");
  }

  function renderRail() {
    $("#rail").innerHTML = STAGES.map(([key, label, ic]) => {
      const sd = st.stages[key];
      const C = 2 * Math.PI * 14;
      const pct = sd.state === "done" ? 100 : sd.pct || 0;
      const status = sd.state === "done" ? (sd.ended && sd.started ? fmtDur(sd.ended - sd.started) : "Done")
        : sd.state === "active" ? (key === "review" ? "Waiting for you" : `${Math.round(pct)}%`)
        : sd.state === "error" ? "Stopped" : "";
      const glyph = sd.state === "done" ? "check" : sd.state === "error" ? "x" : ic;
      return `<div class="step ${sd.state}"><div class="dial">
          <svg class="ring" viewBox="0 0 32 32"><circle class="track" cx="16" cy="16" r="14"/>
            <circle class="bar" cx="16" cy="16" r="14" stroke-dasharray="${C}" stroke-dashoffset="${sd.state === "active" ? C * (1 - pct / 100) : C}"/></svg>
          ${icon(glyph)}</div>
        <span class="lab">${label}</span><span class="st">${esc(status)}</span></div>`;
    }).join("");
  }

  function currentStage() {
    for (const [key] of STAGES) if (["active", "error"].includes(st.stages[key].state)) return key;
    return st.status === "done" ? "render" : "download";
  }

  function renderNow() {
    const key = currentStage();
    const sd = st.stages[key];
    const C = 2 * Math.PI * 52;
    const done = st.status === "done";
    const failed = ["error", "cancelled", "interrupted"].includes(st.status);
    const review = st.status === "review";
    let pct = done ? 100 : sd.pct || 0;
    let msg = sd.msg || STAGE_TITLE[key];
    let eyebrow = STAGE_TITLE[key];
    if (st.status === "queued") { msg = "Waiting for the run before this one"; eyebrow = "Queued"; }
    if (review) { msg = "Claude's picks are ready. Keep the ones you like, then render."; eyebrow = "Your review"; }
    if (done) { msg = "Every clip is rendered and waiting in your Library."; eyebrow = "Finished"; }
    if (failed) { eyebrow = STATUS_LABEL[st.status]; msg = sd.msg && sd.msg !== "Cancelled" ? sd.msg : "Press Resume to continue where it stopped."; }
    const indet = sd.state === "active" && pct < 1 && !review;
    const el = sd.started ? Date.now() / 1000 - sd.started : 0;
    const n = (st.clips || []).length;
    const center = review ? `<div><div class="pct">${n}</div><div class="t-cap secondary">clips</div></div>`
      : done ? `<div class="pct" style="color:var(--green)">${icon("check")}</div>`
      : `<div class="pct">${Math.round(pct)}<small>%</small></div>`;
    const ringCls = done ? "done" : failed ? "err" : indet ? "indet" : "";
    const offset = indet ? C * 0.75 : C * (1 - (review ? 100 : pct) / 100);
    const foot = review
      ? `<button class="btn primary" id="toPicks">Review ${n} Clip${n === 1 ? "" : "s"}</button>`
      : `<div class="foot">${sd.state === "active" && el ? `<span>Elapsed <b>${fmtDur(el)}</b></span>` : ""}${eta(sd) ? `<span>About <b>${eta(sd)}</b> left</span>` : ""}</div>`;
    $("#now").innerHTML = `<div class="big-ring ${ringCls}"><svg viewBox="0 0 120 120"><circle class="track" cx="60" cy="60" r="52"/>
        <circle class="bar" cx="60" cy="60" r="52" stroke-dasharray="${C}" stroke-dashoffset="${offset}"/></svg><div class="center">${center}</div></div>
      <div><div class="t-eyebrow eyebrow">${esc(eyebrow)}</div><p class="msg">${esc(msg)}</p>${foot}</div>`;
    const tp = $("#toPicks");
    if (tp) tp.onclick = () => $("#clipsSec").scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth" });
    const overall = STAGES.reduce((a, [k]) => a + (st.stages[k].state === "done" ? 100 : st.stages[k].pct || 0), 0) / STAGES.length;
    document.title = done ? "Done · Clipper" : review ? "Review · Clipper" : failed ? `${STATUS_LABEL[st.status]} · Clipper`
      : `${Math.round(overall)}% · ${STAGE_TITLE[key]} · Clipper`;
  }

  function renderFeed() {
    const items = [...(st.activity || [])].reverse();
    $("#feedCount").textContent = items.length ? `${items.length} events` : "";
    $("#feed").innerHTML = items.length
      ? items.map((a, i) => {
          const [ic, color] = FEED[a.kind] || FEED.info;
          return `<li class="${i === 0 ? "fresh" : ""}"><span class="tile sm ${color}">${icon(ic)}</span>
            <span class="tx ${a.kind === "claude" ? "quote" : ""}">${esc(a.text)}</span>
            <span class="ft">${new Date(a.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span></li>`;
        }).join("")
      : `<li><span class="tx tertiary">Starting…</span></li>`;
  }

  async function renderHeat() {
    if (!st.heat) return;
    if (!heat && !heatLoading) {
      heatLoading = true;
      try { heat = await api("GET", `/api/runs/${id}/heat`); } catch (e) { heatLoading = false; return; }
    }
    if (!heat) return;
    const W = 1000, H = 110, n = heat.bars.length, bw = W / n, dur = heat.duration;
    const x = (t) => (t / dur) * W;
    let svg = `<svg viewBox="0 0 ${W} ${H + 12}" preserveAspectRatio="none" role="img" aria-label="Hype over the stream">`;
    heat.bars.forEach((v, i) => {
      const h = Math.max(1.5, v * H);
      svg += `<rect class="hb" x="${(i * bw).toFixed(2)}" y="${(H - h).toFixed(2)}" width="${Math.max(0.6, bw - 0.8).toFixed(2)}" height="${h.toFixed(2)}" rx="1" opacity="${(0.1 + 0.42 * v).toFixed(2)}"/>`;
    });
    (st.clips || []).forEach((c) => {
      const off = st.status === "review" ? !selection.has(c.id) : c.selected === false;
      svg += `<rect class="pick ${off ? "off" : ""}" x="${x(c.start).toFixed(1)}" y="0" width="${Math.max(3, x(c.end) - x(c.start)).toFixed(1)}" height="${H}" rx="3"><title>${esc(c.id + ": " + (c.hook || "").replace(/\*/g, ""))}</title></rect>`;
    });
    (st.candidates || []).forEach((c) => {
      svg += `<line class="cand" x1="${x(c.peak).toFixed(1)}" x2="${x(c.peak).toFixed(1)}" y1="${H + 5}" y2="${H + 11}"><title>${esc(`${c.id} at ${fmtTs(c.peak)}`)}</title></line>`;
    });
    svg += `<line class="axis" x1="0" x2="${W}" y1="${H + 0.5}" y2="${H + 0.5}"/></svg>`;
    const box = $("#heat");
    box.innerHTML = svg + `<div class="heat-ticks">${[0, 1, 2, 3, 4].map((k) => `<span>${fmtTs((k / 4) * dur)}</span>`).join("")}</div><div class="heat-tip" hidden></div>`;
    $("#heatLegend").textContent = `${(st.candidates || []).length} moments · ${(st.clips || []).length} picked`;
    const tip = $(".heat-tip", box);
    box.onpointermove = (e) => {
      const r = box.getBoundingClientRect();
      const t = ((e.clientX - r.left) / r.width) * dur;
      tip.hidden = false;
      tip.style.left = `${e.clientX - r.left}px`;
      tip.style.top = `${e.clientY - r.top - 6}px`;
      const inClip = (st.clips || []).find((c) => t >= c.start && t <= c.end);
      tip.textContent = fmtTs(t) + (inClip ? ` · ${inClip.id}` : "");
    };
    box.onpointerleave = () => { tip.hidden = true; };
  }

  function renderClips() {
    const sec = $("#clipsSec");
    const clips = st.clips || [];
    if (!clips.length) { sec.innerHTML = ""; return; }
    const review = st.status === "review";
    const drafting = st.stages.pick.state === "active";
    const cards = clips.map((c) => {
      const sel = review ? selection.has(c.id) : c.selected !== false;
      const r = c.render;
      let foot = "";
      if (review) {
        foot = `<label class="include"><span>Render this clip</span><span class="switch"><input type="checkbox" role="switch" data-sel="${esc(c.id)}" ${sel ? "checked" : ""}><span></span></span></label>`;
      } else if (r && r.step === "done") {
        foot = `<div class="ready">${c.pk ? `<img src="/media/clips/${c.pk}/thumb" alt="">` : ""}<div><div class="t-headline" style="color:var(--green)">Ready</div><a href="#/library">View in Library ${icon("chevron")}</a></div></div>`;
      } else if (r) {
        foot = `<div class="prog"><i style="width:${r.pct}%"></i></div><div class="prog-meta"><span>${esc(r.msg || r.step)}</span><span>${Math.round(r.pct)}%</span></div>`;
      } else if (c.selected === false && st.stages.review.state === "done") {
        foot = `<span class="t-foot tertiary">Skipped</span>`;
      }
      return `<article class="pick ${sel ? "sel" : "unsel"} ${drafting ? "draft" : ""}">
        <div class="pick-top"><span>${esc(c.id.replace("_", " "))} · ${fmtTs(c.start)} · ${c.duration}s</span>${scoreBadge(c.score, c.reason)}</div>
        <div class="hook">${hookHtml(c.hook)}</div>
        ${c.title ? `<div class="title">${esc(c.title)}</div>` : ""}
        ${c.reason ? `<div class="why">${esc(c.reason)}</div>` : ""}
        ${(c.hashtags || []).length ? `<div class="tags">${esc(c.hashtags.join(" "))}</div>` : ""}
        <div class="pick-foot">${foot}</div></article>`;
    }).join("");
    const title = review ? "Choose what to render" : drafting ? "Claude is drafting…" : "Clips";
    sec.innerHTML = `<div class="clips-head"><h2 class="t-title">${title}</h2>
        ${review ? `<div class="run-actions"><button class="btn small" id="selAll">Select All</button><button class="btn small" id="selNone">Select None</button></div>` : ""}</div>
      <div class="picks">${cards}</div>
      ${review ? `<div class="float-bar"><span><b class="num">${selection.size}</b> of ${clips.length} selected</span>
        <button class="btn primary large" id="bRender" ${selection.size ? "" : "disabled"}>Render ${selection.size} Clip${selection.size === 1 ? "" : "s"}</button></div>` : ""}`;
    if (review) {
      $$("[data-sel]", sec).forEach((cb) => cb.addEventListener("change", () => {
        cb.checked ? selection.add(cb.dataset.sel) : selection.delete(cb.dataset.sel);
        // update in place so the switch keeps its own spring animation
        const card = cb.closest(".pick");
        card.classList.toggle("sel", cb.checked); card.classList.toggle("unsel", !cb.checked);
        $(".float-bar b", sec).textContent = selection.size;
        const btn = $("#bRender");
        btn.disabled = !selection.size;
        btn.textContent = `Render ${selection.size} Clip${selection.size === 1 ? "" : "s"}`;
        renderHeat();
      }));
      $("#selAll").onclick = () => { clips.forEach((c) => selection.add(c.id)); renderClips(); renderHeat(); };
      $("#selNone").onclick = () => { selection.clear(); renderClips(); renderHeat(); };
      $("#bRender").onclick = async (e) => {
        e.currentTarget.disabled = true;
        try { await api("POST", `/api/runs/${id}/render`, { clip_ids: [...selection] }); }
        catch (err) { toast(err.message, true); e.currentTarget.disabled = false; }
      };
    }
  }

  function update() {
    if (st.status === "review" && !selectionInit && (st.clips || []).length) {
      st.clips.forEach((c) => { if (c.selected !== false) selection.add(c.id); });
      selectionInit = true;
    }
    const reviewing = st.status === "review" && lastStatus === "review";
    renderHead(); renderRail(); renderNow(); renderFeed(); renderHeat();
    if (!reviewing) renderClips();   // during review the cards are only changed in place (no re-render under the pointer)
    $("#logPre").textContent = (st.log || []).join("\n");
    if (lastStatus && lastStatus !== st.status) {
      if (st.status === "review") notify("Clips ready for review", st.video ? st.video.title : "");
      if (st.status === "done") notify("Clips rendered", st.video ? st.video.title : "");
      if (st.status === "error") notify("Clipping failed", st.error || "");
      refreshOverview();
    }
    lastStatus = st.status;
  }

  return () => { es.close(); clearInterval(tick); };
}

// ------------------------------------------------------------------ page: library
const UP = [["tiktok", "TikTok"], ["reels", "Reels"], ["shorts", "Shorts"]];
const MISSING = [["", "All"], ["any", "Not Posted"], ["tiktok", "Needs TikTok"], ["reels", "Needs Reels"], ["shorts", "Needs Shorts"]];

function viewLibrary() {
  document.title = "Library · Clipper";
  let data = null;
  const f = { q: "", platform: "", missing: "", sort: "new" };
  try { Object.assign(f, JSON.parse(localStorage.getItem("libFilters") || "{}")); } catch (e) { /* ignore */ }

  mount(`<div class="page-head"><h1 class="t-large">Library</h1><p>Everything you've clipped, ready to post. Click a thumbnail to find the file in Explorer.</p></div>
    <div class="summary" id="stats"></div>
    <div class="toolbar">
      <div class="search-wrap">${icon("search")}<input class="search" id="fq" type="search" placeholder="Search" aria-label="Search clips" value="${esc(f.q)}"></div>
      <div class="seg" id="seg" role="tablist" aria-label="Upload filter"><span class="thumb-pill"></span>
        ${MISSING.map(([k, label]) => `<button role="tab" data-missing="${k}" class="${f.missing === k ? "on" : ""}" aria-selected="${f.missing === k}">${label}</button>`).join("")}</div>
      <select class="menu-select" id="fp" aria-label="Platform"><option value="">All Platforms</option><option value="twitch">Twitch</option><option value="youtube">YouTube</option><option value="kick">Kick</option><option value="local">Local</option></select>
      <select class="menu-select" id="fs" aria-label="Sort"><option value="new">Clip Order</option><option value="score">Highest Score</option></select>
      <button class="icon-btn" id="rescan" title="Rescan job folders" aria-label="Rescan job folders">${icon("resume")}</button>
    </div>
    <div id="lib"><div class="empty">Loading…</div></div>`);
  $("#fp").value = f.platform; $("#fs").value = f.sort;
  const save = () => { try { localStorage.setItem("libFilters", JSON.stringify(f)); } catch (e) { /* ignore */ } };

  const seg = $("#seg");
  const placePill = (animate) => {
    const on = $("button.on", seg) || $("button", seg);
    const pill = $(".thumb-pill", seg);
    if (!animate) pill.style.transition = "none";
    pill.style.left = `${on.offsetLeft}px`;
    pill.style.width = `${on.offsetWidth}px`;
    if (!animate) { pill.offsetWidth; pill.style.transition = ""; }
  };
  requestAnimationFrame(() => placePill(false));
  seg.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-missing]");
    if (!b) return;
    $$("button", seg).forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-selected", x === b); });
    f.missing = b.dataset.missing; save(); placePill(true); render();
  });
  addEventListener("resize", () => placePill(false));
  const bar = $(".toolbar");
  const onScroll = () => bar.classList.toggle("stuck", bar.getBoundingClientRect().top <= $("#nav").offsetHeight + 0.5 && scrollY > 0);
  addEventListener("scroll", onScroll, { passive: true });
  $("#fq").addEventListener("input", (e) => { f.q = e.target.value; save(); render(); });
  $("#fp").addEventListener("change", (e) => { f.platform = e.target.value; save(); render(); });
  $("#fs").addEventListener("change", (e) => { f.sort = e.target.value; save(); render(); });
  $("#rescan").addEventListener("click", async () => {
    try { const r = await api("POST", "/api/rescan"); toast(`Found ${r.clips} clips`); load(); } catch (e) { toast(e.message, true); }
  });

  const tags = (c) => c.hashtags.map((h) => (h.startsWith("#") ? h : "#" + h)).join(" ");
  const fullDesc = (c, v) => [c.description.trim(), v.effective_link, tags(c)].filter(Boolean).join("\n\n");

  function matches(c, v, creator) {
    if (f.platform && v.platform !== f.platform) return false;
    if (f.missing === "any" && Object.keys(c.uploads).length) return false;
    if (f.missing && f.missing !== "any" && c.uploads[f.missing]) return false;
    if (f.q) {
      const hay = [c.title, c.description, tags(c), c.hook, v.title, creator.channel].join(" ").toLowerCase();
      if (!f.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w))) return false;
    }
    return true;
  }

  const block = (k, cls, text, kind) => `<div class="meta-block"><div class="k">${k}</div><div class="v ${cls}">${text}</div>
      <button class="icon-btn" data-copy="${kind}" aria-label="Copy ${k.toLowerCase()}" title="Copy">${icon("copy")}</button></div>`;

  function clipCard(c, v) {
    const all = UP.every(([k]) => c.uploads[k]);
    return `<article class="clip ${all ? "all-posted" : ""}" data-clip="${c.pk}">
      <button class="thumb" data-act="reveal" aria-label="Show ${esc(c.clip_id)}.mp4 in Explorer">
        ${c.has_thumb ? `<img src="/media/clips/${c.pk}/thumb" alt="" loading="lazy">` : ""}
        ${scoreBadge(c.score, c.reason)}<span class="len">${fmtTs(c.duration)}</span>
        <span class="play" data-act="play" role="button" aria-label="Play ${esc(c.title)}">${PLAY}</span>
        <span class="reveal">${icon("folder")} Show in Explorer</span>
      </button>
      <div class="cbody">
        ${block("Title", "title", esc(c.title), "title")}
        ${block("Description", "desc", esc(fullDesc(c, v)), "desc")}
        ${block("Hashtags", "tags", esc(tags(c)) || '<span class="tertiary">None</span>', "tags")}
        ${c.reason ? `<div class="why">${esc(c.reason)}</div>` : ""}
        <div class="posts" role="group" aria-label="Posted to">${UP.map(([k, label]) => `<button class="post ${k} ${c.uploads[k] ? "on" : ""}" data-up="${k}"
            aria-pressed="${!!c.uploads[k]}" title="${c.uploads[k] ? "Posted " + new Date(c.uploads[k] * 1000).toLocaleString() : "Mark as posted to " + label}">
            <span class="ck">${icon("check")}</span>${label}</button>`).join("")}</div>
      </div></article>`;
  }

  function render() {
    if (!data) return;
    let total = 0;
    const upCount = { tiktok: 0, reels: 0, shorts: 0 };
    const html = data.creators.map((cr) => {
      const vids = cr.videos.map((v) => {
        total += v.clips.length;
        v.clips.forEach((c) => UP.forEach(([k]) => { if (c.uploads[k]) upCount[k]++; }));
        let clips = v.clips.filter((c) => matches(c, v, cr));
        if (!clips.length) return "";
        if (f.sort === "score") clips = [...clips].sort((a, b) => (b.score || 0) - (a.score || 0));
        const n = v.clips.length;
        const bars = UP.map(([k, label]) => {
          const d = v.clips.filter((c) => c.uploads[k]).length;
          return `<span class="pbar ${k}">${label} ${d}/${n}<i><b style="width:${(100 * d) / n}%"></b></i></span>`;
        }).join("");
        const want = sessionStorage.getItem("openVideo");
        const open = !want || want == v.pk;
        return `<details class="video" data-video="${v.pk}" ${open ? "open" : ""}>
          <summary><span class="vt">${esc(v.title)}</span>
            <span class="vs">${capsule(v.platform)}<span>${n} clip${n === 1 ? "" : "s"}</span><span class="tertiary">·</span><span>${timeAgo(v.created_at)}</span>
              ${v.url && v.url.startsWith("http") ? `<span class="tertiary">·</span><a href="${esc(v.url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">Source</a>` : ""}</span>
            <span class="posted-bars">${bars}</span><span class="chev">${icon("chevron")}</span></summary>
          <div class="video-body">
            <div class="linkrow">${icon("link")}<span>Link in descriptions</span>
              <input class="field-input" data-link="${v.pk}" value="${esc(v.desc_link === null ? data.default_link : v.desc_link)}" placeholder="No link" aria-label="Link in descriptions">
              <button class="btn small" data-linksave="${v.pk}">Save</button>
              ${v.desc_link !== null ? `<button class="btn small plain" data-linkreset="${v.pk}">Use Default</button>` : `<span class="tertiary">${data.default_link ? "Default" : ""}</span>`}
            </div>
            <div class="clips-grid">${clips.map((c) => clipCard(c, v)).join("")}</div>
          </div></details>`;
      }).join("");
      if (!vids) return "";
      return `<section class="creator"><div class="creator-head"><span class="avatar ${esc(cr.platform)}">${esc((cr.channel || "?")[0].toUpperCase())}</span>
        <div><h2 class="t-title2">${esc(cr.channel)}</h2><div class="t-foot secondary">${esc(platformName(cr.platform))}</div></div></div>${vids}</section>`;
    }).join("");
    sessionStorage.removeItem("openVideo");
    const stat = (label, value, ic, color) => `<div class="surface stat"><div class="k"><span class="tile sm ${color}">${icon(ic)}</span>${label}</div>
      <div class="v">${value}${total && label !== "Clips" ? `<small> / ${total}</small>` : ""}</div></div>`;
    $("#stats").innerHTML = stat("Clips", total, "grid", "blue") + stat("TikTok", upCount.tiktok, "check", "teal")
      + stat("Reels", upCount.reels, "check", "pink") + stat("Shorts", upCount.shorts, "check", "red");
    $("#lib").innerHTML = html || `<div class="list"><div class="empty">${total
      ? "No clips match these filters."
      : `No clips yet. <a href="#/new">Clip a stream</a> to fill your Library.`}</div></div>`;
  }

  function findClip(pk) {
    for (const cr of data.creators) for (const v of cr.videos) for (const c of v.clips) if (c.pk === pk) return { c, v };
    return null;
  }

  $("#lib").addEventListener("click", async (e) => {
    const card = e.target.closest("[data-clip]");
    const copyBtn = e.target.closest("[data-copy]");
    const upBtn = e.target.closest("[data-up]");
    const act = e.target.closest("[data-act]");
    const linkSave = e.target.closest("[data-linksave]");
    const linkReset = e.target.closest("[data-linkreset]");
    if (linkSave || linkReset) {
      const pk = parseInt(linkSave ? linkSave.dataset.linksave : linkReset.dataset.linkreset, 10);
      const value = linkReset ? null : $(`[data-link="${pk}"]`).value.trim();
      try { await api("PUT", `/api/videos/${pk}`, { desc_link: value }); toast(linkReset ? "Using your default link" : "Link saved"); load(); }
      catch (err) { toast(err.message, true); }
      return;
    }
    if (!card) return;
    const pk = parseInt(card.dataset.clip, 10);
    const found = findClip(pk);
    if (!found) return;
    const { c, v } = found;
    if (copyBtn) {
      const kind = copyBtn.dataset.copy;
      copyText(kind === "title" ? c.title : kind === "desc" ? fullDesc(c, v) : tags(c), copyBtn);
      return;
    }
    if (upBtn) {
      const k = upBtn.dataset.up;
      const on = !c.uploads[k];
      on ? (c.uploads[k] = Date.now() / 1000) : delete c.uploads[k];
      // update in place: the checkmark animates instead of the card being rebuilt
      upBtn.classList.toggle("on", on);
      upBtn.setAttribute("aria-pressed", on);
      card.classList.toggle("all-posted", UP.every(([x]) => c.uploads[x]));
      try {
        const r = await api("POST", `/api/clips/${pk}/uploads/${k}`, { on });
        if (on) c.uploads[k] = r.uploaded_at;
        clearTimeout(viewLibrary._rt);
        viewLibrary._rt = setTimeout(render, 450);   // refresh counters once the checkmark spring has settled
      } catch (err) {
        on ? delete c.uploads[k] : (c.uploads[k] = Date.now() / 1000);
        render(); toast(err.message, true);
      }
      return;
    }
    if (act && act.dataset.act === "play") {
      e.stopPropagation();
      openPlayer(pk, act.closest(".thumb"));
      return;
    }
    if (act && act.dataset.act === "reveal") {
      try { await api("POST", `/api/clips/${pk}/reveal`); toast("Shown in Explorer"); } catch (err) { toast(err.message, true); }
    }
  });

  async function load() {
    try { data = await api("GET", "/api/library"); render(); }
    catch (e) { $("#lib").innerHTML = `<div class="banner err">${icon("warn")}<span>${esc(e.message)}</span></div>`; }
  }
  load();
  const onFocus = () => load();
  addEventListener("focus", onFocus);
  return () => { removeEventListener("focus", onFocus); removeEventListener("scroll", onScroll); };
}

// ------------------------------------------------------------------ video player: grows out of the thumbnail, returns to it
let playerFrom = null;
function sheetTransform(from, sheet) {
  const a = from.getBoundingClientRect(), b = sheet.getBoundingClientRect();
  const dx = a.left + a.width / 2 - (b.left + b.width / 2), dy = a.top + a.height / 2 - (b.top + b.height / 2);
  return `translate(${dx}px, ${dy}px) scale(${a.width / b.width})`;
}
function openPlayer(pk, fromEl) {
  const dlg = $("#player"), sheet = $(".sheet", dlg), vid = $("video", dlg);
  playerFrom = fromEl;
  vid.src = `/media/clips/${pk}/video`;
  dlg.showModal();
  requestAnimationFrame(() => dlg.classList.add("open"));
  if (!reducedMotion() && fromEl) {
    sheet.animate([{ transform: sheetTransform(fromEl, sheet), borderRadius: "0.8rem", opacity: 0.85 }, { transform: "none", borderRadius: "1.6rem", opacity: 1 }],
      { duration: 500, easing: SPRING() });
  } else {
    sheet.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 200 });
  }
  vid.play().catch(() => {});
}
async function closePlayer() {
  const dlg = $("#player"), sheet = $(".sheet", dlg), vid = $("video", dlg);
  if (!dlg.open) return;
  vid.pause();
  dlg.classList.remove("open");
  const anim = !reducedMotion() && playerFrom && playerFrom.isConnected
    ? sheet.animate([{ transform: "none", opacity: 1 }, { transform: sheetTransform(playerFrom, sheet), borderRadius: "0.8rem", opacity: 0.6 }],
        { duration: 380, easing: SPRING() })
    : sheet.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 180 });
  // never let a throttled animation clock (background tab) hold the sheet open
  await Promise.race([anim.finished.catch(() => {}), new Promise((r) => setTimeout(r, 450))]);
  anim.cancel();
  vid.removeAttribute("src"); vid.load();
  dlg.close();
}
(() => {
  const dlg = $("#player");
  $(".close", dlg).addEventListener("click", closePlayer);
  dlg.addEventListener("click", (e) => { if (e.target === dlg) closePlayer(); });
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); closePlayer(); });
})();

// ------------------------------------------------------------------ page: settings
async function viewSettings() {
  document.title = "Settings · Clipper";
  const ov = await refreshOverview();
  const ok = (good, yes, no) => good ? `<span class="ok-text">${yes}</span>` : `<span class="err-text">${no}</span>`;
  mount(`<div class="settings"><div class="page-head"><h1 class="t-large">Settings</h1></div>
    <div class="group-label">DESCRIPTIONS</div>
    <form class="list" id="sForm">
      <div class="setting-row"><span class="tile blue">${icon("link")}</span>
        <input class="field-input" id="sLink" placeholder="https://linktr.ee/you" value="${esc(ov ? ov.settings.desc_link : "")}" aria-label="Default description link">
        <button class="btn primary small" type="submit">Save</button></div>
    </form>
    <div class="group-foot">Added between the description and the hashtags of every clip. Leave it empty for none. You can still change it per link and per video.</div>
    <div class="group-label">STATUS</div>
    <div class="list">
      <div class="setting-row"><span class="tile indigo">${icon("terminal")}</span><span>Claude Code</span><span class="v">${ok(ov && ov.claude_found, "Installed", "Not found")}</span></div>
      <div class="setting-row"><span class="tile green">${icon("card")}</span><span>Billing</span><span class="v">${ov && ov.api_key_set ? "Pro plan (API key ignored)" : "Pro plan"}</span></div>
      ${ov && ov.fake_picker ? `<div class="setting-row"><span class="tile orange">${icon("sparkles")}</span><span>Test mode</span><span class="v">Fake picker</span></div>` : ""}
      <div class="setting-row"><span class="tile gray">${icon("slider")}</span><span>Look and quality</span><span class="v mono">config.yaml</span></div>
    </div>
    <div class="group-foot">${ov && !ov.claude_found ? "Install Claude Code, run <span class='mono'>claude</span> once and type <span class='mono'>/login</span>." : "Everything stays on this computer: clips in jobs/, the library in jobs/library.db."}</div>
  </div>`);
  $("#sForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("PUT", "/api/settings", { desc_link: $("#sLink").value }); toast("Saved"); refreshOverview(); }
    catch (err) { toast(err.message, true); }
  });
}
