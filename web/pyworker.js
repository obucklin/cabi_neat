// The planner's Python, in a worker so the page stays responsive while it builds the cabinet.
//
// Loads Pyodide, the packages compas needs (numpy, scipy, shapely) and the site's wheels (compas, compas_timber,
// cabi_neat; see wheels/manifest.json, written by build.py), then answers the page's messages:
//   {id, kind: "request", method, path, body}  -> {id, answer, meta}   (cabi_neat.web.WebPlanner.request_json)
//   {id, kind: "scene"}                        -> {id, scene}          (WebPlanner.scene)
//   {id, kind: "pick", name}                   -> {id}
// `meta` is {revision, highlight}: what the 3D view should show. While loading it posts {progress: text}.
import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/pyodide.mjs";

const progress = (text) => postMessage({ progress: text });

async function start() {
  progress("Loading Python…");
  const py = await loadPyodide();
  progress("Loading numpy, scipy and shapely…");
  await py.loadPackage(["micropip", "numpy", "scipy", "shapely"]);
  progress("Loading compas_timber and the planner…");
  const base = new URL("wheels/", self.location.href);
  const manifest = await (await fetch(new URL("manifest.json", base))).json();
  // the wheels' own requirements are the packages above, or not needed here (no OpenCascade, rtree, ...)
  await py.pyimport("micropip").install.callKwargs(
    manifest.wheels.map((name) => new URL(name, base).href),
    { deps: false },
  );
  progress("Starting…");
  return py.runPython("from cabi_neat.web import WebPlanner\nWebPlanner()");
}

const ready = start();
ready.then(
  () => postMessage({ ready: true }),
  (e) => postMessage({ failed: String(e && e.message ? e.message : e) }),
);

function meta(planner) {
  return JSON.parse(planner.view_state());
}

// one message at a time, in order: Python isn't reentrant
let queue = Promise.resolve();
self.onmessage = (event) => {
  queue = queue.then(() => handle(event.data));
};

async function handle({ id, kind, method, path, body, name }) {
  try {
    const planner = await ready;
    if (kind === "request") {
      const answer = planner.request_json(method, path, body ?? null);
      postMessage({ id, answer, meta: meta(planner) });
    } else if (kind === "scene") {
      postMessage({ id, scene: planner.scene() });
    } else if (kind === "pick") {
      planner.pick(name);
      postMessage({ id });
    }
  } catch (e) {
    postMessage({ id, error: String(e && e.message ? e.message : e) });
  }
}
