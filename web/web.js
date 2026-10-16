// The planner as a static site: the planner page as it is, with its Python running in the browser.
//
// The page talks to its backend with fetch("api/..."). Here those calls go to cabi_neat.web.WebPlanner, running in
// Pyodide in a worker (pyworker.js), instead of a server; everything else is fetched as usual. The 3D view is
// viewer3d.js instead of the compas_threejs viewer: after each call it shows what the planner's state says it should.
// This module must load before planner.js, which calls the API as soon as it starts.
import { createViewer } from "./viewer3d.js";

const worker = new Worker(new URL("./pyworker.js", import.meta.url), { type: "module" });
const pending = new Map();
let nextId = 0;

const boot = document.getElementById("boot");
const bootStatus = document.getElementById("boot-status");
let started = false;

worker.onmessage = ({ data }) => {
  if (data.progress) bootStatus.textContent = data.progress;
  else if (data.ready) {
    started = true;
    boot.hidden = true;
  } else if (data.failed) {
    bootStatus.textContent = `The planner couldn't start: ${data.failed}`;
    boot.classList.add("failed");
  } else if (pending.has(data.id)) {
    const { resolve, reject } = pending.get(data.id);
    pending.delete(data.id);
    if (data.error) reject(new Error(data.error));
    else resolve(data);
  }
};

function call(message) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    worker.postMessage({ id, ...message });
  });
}

// ============================================================================
// the 3D view
// ============================================================================

const view = document.getElementById("view3d");
const viewer = createViewer(view, { onPick: (name) => call({ kind: "pick", name }) });
let shown = null;

/** Show what the planner's state says the 3D view should: its parts (if they changed) and the highlighted ones. */
async function update3d(meta) {
  if (meta.revision !== shown) {
    shown = meta.revision;
    const { scene } = await call({ kind: "scene" });
    viewer.show(JSON.parse(scene));
  }
  viewer.highlight(meta.highlight);
}

// ============================================================================
// the API, answered in the browser
// ============================================================================

const realFetch = window.fetch.bind(window);
const apiBase = new URL("api/", location.href).href;

window.fetch = async (input, init = {}) => {
  const url = new URL(typeof input === "string" ? input : input.url, location.href);
  if (!url.href.startsWith(apiBase)) return realFetch(input, init);
  const method = (init.method || "GET").toUpperCase();
  const { answer, meta } = await call({ kind: "request", method, path: url.href.slice(apiBase.length - 4), body: init.body ?? null });
  update3d(meta).catch((e) => console.error(e));
  const reply = JSON.parse(answer);
  if ("json" in reply) return new Response(JSON.stringify(reply.json), { status: reply.status, headers: { "Content-Type": "application/json" } });
  // binary answers (exports) come as Latin-1 text of their bytes
  const bytes = Uint8Array.from(reply.body, (c) => c.charCodeAt(0));
  return new Response(bytes, { status: reply.status, headers: { ...reply.headers, "Content-Type": reply.media_type || "application/octet-stream" } });
};

// the first state may take a while: say so if loading takes longer than a moment
setTimeout(() => {
  if (!started) boot.classList.add("slow");
}, 4000);
