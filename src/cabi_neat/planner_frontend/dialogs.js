// Popup dialogs: a joint's settings (its geometry, and the HOPS export's tool and fit tolerances for the cuts it makes),
// and the HOPS export's global settings, shown before writing the programs.
//
// The HOPS settings are the server's (`api/hops-settings`): the machine's tools, the defaults per operation, how parts
// are cut to size. A joint's own machining settings are part of the cabinet's config; empty fields fall back to what
// the joint inherits, shown as placeholders.

const STORAGE_KEY = "cabi_neat.hops_settings";
const JSON_TYPE = { "application/json": [".json"] };

// the tolerance fields each operation has: [key, label]
const DIAMETER = [
  ["width", "Ø +"],
  ["depth", "Depth +"],
];
const POCKET = [
  ["width", "Width +"],
  ["length", "Length +"],
  ["depth", "Depth +"],
];
export const TOLERANCE_FIELDS = {
  groove: POCKET,
  rabbet: [
    ["width", "Width +"],
    ["depth", "Depth +"],
  ],
  mortise: POCKET,
  dado: POCKET,
  pocket: POCKET,
  tenon_notch: [
    ["length", "Length +"],
    ["depth", "Depth +"],
  ],
  pot: DIAMETER,
  drill: DIAMETER,
  shelf_hole: DIAMETER,
  horizontal_drill: DIAMETER,
};
const TOLERANCE_HINTS = {
  width: "Added across the cut, or to the diameter (mm). Negative is smaller.",
  length: "Added along the cut: its closed ends move by half each (mm). For tenon notches: shorter tenons.",
  depth: "Added to the depth (mm)",
};
// what each operation is, in a joint's dialog
const OPERATION_TEXT = {
  groove: "Groove",
  rabbet: "Rabbet",
  mortise: "Mortises",
  dado: "Dado",
  pocket: "Pocket",
  tenon_notch: "Notches beside the tenons",
  pot: "Pots",
  drill: "Holes",
  shelf_hole: "Shelf holes",
  horizontal_drill: "Holes in edges",
};

function esc(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function num(value) {
  return Math.round(value * 100) / 100;
}

function storageGet() {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function storageSet(text) {
  try {
    localStorage.setItem(STORAGE_KEY, text);
  } catch {
    // private window or storage blocked: the settings just aren't remembered
  }
}

// ============================================================================
// the HOPS settings, as the server has them
// ============================================================================

let hops = null; // {settings, defaults, operations, router_operations, labels, contours}

/** The server's HOPS settings (loaded once; the export dialog keeps them current). */
export async function hopsSettings() {
  if (!hops) {
    hops = await (await fetch("api/hops-settings")).json();
    // the settings last used in this browser, unless the server was started with some
    const stored = storageGet();
    if (stored && JSON.stringify(hops.settings) === JSON.stringify(hops.defaults)) {
      try {
        await postSettings(JSON.parse(stored), false);
      } catch {
        // old or broken: keep the defaults
      }
    }
  }
  return hops;
}

/** Make `settings` the server's. Resolves to the error message, or null on success. */
async function postSettings(settings, remember = true) {
  const res = await fetch("api/hops-settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings) });
  const data = await res.json();
  if (!data.ok) return data.error || "The HOPS settings were not accepted.";
  hops = data;
  if (remember) storageSet(JSON.stringify(data.settings));
  return null;
}

/** The tools that could do `operation`: routers, or drills (with the drill head's bits for shelf holes). */
function toolsFor(settings, operation) {
  const t = settings.tools;
  if (hops.router_operations.includes(operation)) return t.routers;
  return operation === "shelf_hole" ? [...t.drill_head_bits, ...t.drills] : t.drills;
}

function toolLabel(t) {
  return `${t.number} · Ø${num(t.diameter)} · ${num(t.usable_length)} long${t.name ? ` · ${t.name}` : ""}`;
}

function toolSelect(settings, operation, value, attrs, emptyLabel) {
  const tools = toolsFor(settings, operation);
  const missing = value !== undefined && value !== null && !tools.some((t) => t.number === value) ? `<option value="${value}" selected>${value} (not in the tool table)</option>` : "";
  const options = tools.map((t) => `<option value="${t.number}" ${value === t.number ? "selected" : ""}>${esc(toolLabel(t))}</option>`).join("");
  return `<select ${attrs}><option value="">${esc(emptyLabel)}</option>${missing}${options}</select>`;
}

function numberInput(attrs, value, placeholder = "") {
  return `<input type="number" step="any" value="${value === null || value === undefined ? "" : num(value)}" placeholder="${esc(placeholder)}" ${attrs} />`;
}

/** What applies to `operation` without the joint's own settings: `inherited` (the cabinet's), else the HOPS settings'. */
function fallbackOf(settings, operation, inherited) {
  const own = (inherited || {})[operation] || {};
  const global = settings.tolerances[operation] || {};
  const value = (key) => own[key] ?? global[key] ?? 0;
  return { width: value("width"), length: value("length"), depth: value("depth"), tool: own.tool ?? settings.tool_for[operation] ?? null };
}

/** A table of tool and tolerances for `operations`: `values` are the joint's own, `inherited` what applies without. */
function machiningTable(settings, operations, values, inherited) {
  const rows = operations.map((operation) => {
    const own = (values || {})[operation] || {};
    const fallback = fallbackOf(settings, operation, inherited);
    const fields = TOLERANCE_FIELDS[operation] || [];
    const tolerance = ["width", "length", "depth"].map((key) => {
      const field = fields.find(([k]) => k === key);
      if (!field) return "<td></td>";
      return `<td title="${esc(TOLERANCE_HINTS[key])}"><label class="mini">${field[1]} ${numberInput(`data-op="${operation}" data-key="${key}"`, own[key], num(fallback[key]))}</label></td>`;
    });
    const autoLabel = fallback.tool === null ? "Auto (largest that fits)" : `Default: ${fallback.tool}`;
    return `<tr><th scope="row">${esc(OPERATION_TEXT[operation] || operation)}</th>
      <td>${toolSelect(settings, operation, own.tool, `data-op="${operation}" data-key="tool" aria-label="${esc(OPERATION_TEXT[operation])} tool"`, autoLabel)}</td>${tolerance.join("")}</tr>`;
  });
  return `<table class="machining"><thead><tr><th></th><th>Tool</th><th colspan="3">Fit tolerances (mm)</th></tr></thead><tbody>${rows.join("")}</tbody></table>`;
}

/** The machining settings in `form`'s table: `{operation: {width, length, depth, tool}}` without empty values, or null. */
function readMachining(form) {
  const machining = {};
  for (const el of form.querySelectorAll("[data-op]")) {
    if (el.value === "") continue;
    const op = el.dataset.op;
    machining[op] = { ...(machining[op] || {}), [el.dataset.key]: Number(el.value) };
  }
  return Object.keys(machining).length ? machining : null;
}

function showDialog(className, html, onReady) {
  const dialog = document.createElement("dialog");
  dialog.className = `popup ${className}`;
  dialog.innerHTML = html;
  document.body.appendChild(dialog);
  dialog.addEventListener("close", () => dialog.remove());
  if (onReady) onReady(dialog);
  dialog.showModal();
  return dialog;
}

// ============================================================================
// a joint's dialog
// ============================================================================

/**
 * A joint's settings in a popup. Resolves to `{values, machining}` (the fields' values, null where empty, and the
 * joint's own machining settings, null if none) when applied, or null when cancelled.
 *
 * `fields`: `[{key, label, value, placeholder, hint, checkbox, min, heading}]` (a `heading` is a subheading only);
 * `operations`: the cuts the joint makes (see TOLERANCE_FIELDS), with its own settings `machining` and what it
 * inherits without them, `inherited` (`{operation: {...}}`, over the HOPS settings).
 */
export async function jointDialog({ title, intro = "", fields = [], operations = [], machining = null, inherited = null, note = "" }) {
  const { settings } = operations.length ? await hopsSettings() : { settings: null };
  const rows = fields
    .map((f) => {
      if (f.heading) return `<div class="subhead">${esc(f.heading)}</div>`;
      if (f.checkbox) return `<label class="row" title="${esc(f.hint || "")}">${esc(f.label)} <input type="checkbox" data-field="${f.key}" ${f.value ? "checked" : ""} /></label>`;
      return `<label class="row" title="${esc(f.hint || "Empty: the default")}">${esc(f.label)}
        <input type="number" step="any" min="${f.min ?? 0}" data-field="${f.key}" value="${f.value === null || f.value === undefined ? "" : num(f.value)}" placeholder="${esc(f.placeholder ?? "")}" /></label>`;
    })
    .join("");
  const machiningPart = operations.length
    ? `<h3>HOPS export</h3><div class="hint">This joint's tool and fit tolerances. Empty: the default, shown greyed (set in Write HOPS).</div>${machiningTable(settings, operations, machining, inherited)}`
    : "";
  return new Promise((resolve) => {
    let result = null;
    showDialog(
      "joint-dialog",
      `<form method="dialog">
        <h2>${esc(title)}</h2>
        ${intro ? `<div class="hint">${esc(intro)}</div>` : ""}
        ${rows ? `<div class="fields">${rows}</div>` : ""}
        ${machiningPart}
        ${note ? `<div class="hint">${esc(note)}</div>` : ""}
        <div class="buttons end">
          <button type="button" data-clear title="Empty all fields: use the defaults">Reset to defaults</button>
          <button value="cancel" formnovalidate>Cancel</button>
          <button value="apply" class="primary">Apply</button>
        </div>
      </form>`,
      (dialog) => {
        const form = dialog.querySelector("form");
        dialog.querySelector("[data-clear]").addEventListener("click", () => {
          for (const el of form.querySelectorAll("input[type=number], select")) el.value = "";
        });
        form.addEventListener("submit", (evt) => {
          if (evt.submitter && evt.submitter.value === "apply") {
            const values = {};
            for (const el of form.querySelectorAll("[data-field]")) values[el.dataset.field] = el.type === "checkbox" ? el.checked : el.value === "" ? null : Number(el.value);
            result = { values, machining: operations.length ? readMachining(form) : null };
          }
        });
        dialog.addEventListener("close", () => resolve(result));
      },
    );
  });
}

// ============================================================================
// the HOPS export dialog
// ============================================================================

const MACHINE_NUMBERS = [
  ["through_overcut", "Through overcut", "How much deeper than the part through cuts go (mm)"],
  ["drill_head", "Drill head (WZB)", "The drill head's tool number"],
];
const CLAMEX_NUMBERS = [
  ["clamex_disc", "Disc (edges)"],
  ["clamex_finger", "Finger mill (faces)"],
  ["clamex_drill", "Access hole drill"],
  ["clamex_hole_diameter", "Access hole Ø"],
  ["clamex_hole_position", "Access hole position"],
];

function toolTable(title, list) {
  if (!list.length) return `<div class="hint">${title}: none</div>`;
  const rows = list.map((t) => `<tr><td>${t.number}</td><td>${num(t.diameter)}</td><td>${num(t.usable_length)}</td><td class="name">${esc(t.name || "")}</td></tr>`).join("");
  return `<table class="cutlist tools"><caption>${title}</caption><thead><tr><th>No.</th><th>Ø</th><th>Length</th><th>Name</th></tr></thead><tbody>${rows}</tbody></table>`;
}

/**
 * The HOPS export's settings, before writing: resolves to true once `write()` (which writes the programs with the
 * settings now on the server) was called, false if cancelled. `write` must start with asking where to save, while the
 * click still counts as a user gesture. `setMessage(text, kind)` reports.
 */
export async function hopsDialog({ write, setMessage, pickSaveTarget, writeSaveTarget }) {
  await hopsSettings();
  let settings = structuredClone(hops.settings); // the working copy

  function contourPart() {
    const t = settings.tools;
    const router = settings.contour === "router";
    return `<label class="row"><span><input type="checkbox" data-tools-check="format_parts" ${t.format_parts ? "checked" : ""} /> Cut parts to size</span></label>
      ${
        t.format_parts
          ? `<div class="segmented">
        <button type="button" data-contour="saw" class="${router ? "" : "active"}">Saw</button>
        <button type="button" data-contour="router" class="${router ? "active" : ""}">Router along the outline</button></div>
      ${
        router
          ? `<label class="row">Router ${toolSelect(settings, "pocket", settings.contour_tool, 'data-setting="contour_tool"', "Auto (largest that reaches through)")}</label>
        <div class="hint">_Format_V5 for rectangular parts, a contour around any other outline.</div>`
          : `<label class="row">Saw (WZS) ${numberInput('data-tools="saw"', t.saw)}</label>
        <label class="row" title="Lead-in and -out of the saw cuts">Lead-in ${numberInput('data-tools="format_lead"', t.format_lead)}</label>`
      }
      <label class="row" title="TIEFE of the format: negative is below the part's underside">Depth ${numberInput('data-tools="format_depth"', t.format_depth)}</label>`
          : ""
      }`;
  }

  function defaultsPart() {
    const rows = hops.operations.map((operation) => {
      const tolerance = settings.tolerances[operation] || {};
      const fields = TOLERANCE_FIELDS[operation] || [];
      const cells = ["width", "length", "depth"].map((key) => {
        const field = fields.find(([k]) => k === key);
        if (!field) return "<td></td>";
        return `<td title="${esc(TOLERANCE_HINTS[key])}"><label class="mini">${field[1]} ${numberInput(`data-default-op="${operation}" data-key="${key}"`, tolerance[key] || null, "0")}</label></td>`;
      });
      return `<tr><th scope="row">${esc(hops.labels[operation] || operation)}</th>
        <td>${toolSelect(settings, operation, settings.tool_for[operation], `data-default-op="${operation}" data-key="tool"`, "Auto")}</td>${cells.join("")}</tr>`;
    });
    return `<div class="hint">For every joint that doesn't set its own (⚙ next to the joint).</div>
      <table class="machining"><thead><tr><th></th><th>Tool</th><th colspan="3">Fit tolerances (mm)</th></tr></thead><tbody>${rows.join("")}</tbody></table>`;
  }

  function machinePart() {
    const t = settings.tools;
    const numbers = MACHINE_NUMBERS.map(([key, label, hint]) => `<label class="row" title="${esc(hint)}">${label} ${numberInput(`data-tools="${key}"`, t[key])}</label>`).join("");
    const clamex = CLAMEX_NUMBERS.map(([key, label]) => `<label class="row">${label} ${numberInput(`data-tools="${key}"`, t[key])}</label>`).join("");
    return `<div class="buttons"><button type="button" data-action="import-too" title="Read the routers, drills and drill head bits from the machine's tool table">Import tool table (.too)…</button>
      <span class="hint">${t.routers.length} routers, ${t.drills.length} drills, ${t.drill_head_bits.length} drill head bits</span></div>
      <details class="tool-list"><summary>Tools</summary>${toolTable("Routers", t.routers)}${toolTable("Drills", t.drills)}${toolTable("Drill head bits", t.drill_head_bits)}</details>
      ${numbers}
      <div class="subhead">Clamex P-14</div>
      <label class="row"><span><input type="checkbox" data-tools-check="clamex_access_holes" ${t.clamex_access_holes ? "checked" : ""} /> Access holes</span></label>
      ${clamex}`;
  }

  function body() {
    return `<h2>Write HOPS</h2>
      <div class="columns">
        <section><h3>Cutting parts to size</h3>${contourPart()}<h3>Machine</h3>${machinePart()}</section>
        <section><h3>Defaults per machining</h3>${defaultsPart()}</section>
      </div>
      <div class="buttons end">
        <button type="button" data-action="load">Load settings…</button>
        <button type="button" data-action="save">Save settings…</button>
        <button type="button" data-action="reset" class="danger" title="The default tools and no tolerances">Reset</button>
        <span class="grow"></span>
        <button type="button" data-action="cancel">Cancel</button>
        <button type="button" data-action="write" class="primary">Write HOPS…</button>
      </div>
      <input type="file" data-file="too" accept=".too" hidden />
      <input type="file" data-file="settings" accept=".json,application/json" hidden />`;
  }

  return new Promise((resolve) => {
    let written = false;
    showDialog("hops-dialog", `<form>${body()}</form>`, (dialog) => {
      const form = dialog.querySelector("form");
      const render = () => {
        form.innerHTML = body();
      };
      dialog.addEventListener("close", () => resolve(written));
      form.addEventListener("submit", (evt) => evt.preventDefault());

      form.addEventListener("change", async (evt) => {
        const el = evt.target;
        const d = el.dataset;
        const value = el.value === "" ? null : Number(el.value);
        if (d.file) {
          const file = el.files[0];
          el.value = "";
          if (!file) return;
          if (d.file === "too") await importToo(file);
          else {
            try {
              settings = { ...structuredClone(hops.defaults), ...JSON.parse(await file.text()) };
              setMessage(`Loaded the HOPS settings ${file.name}.`);
            } catch (e) {
              setMessage(`Could not read ${file.name}: ${e.message}`, "error");
            }
          }
          render();
          return;
        }
        if (d.tools && value !== null) settings.tools[d.tools] = value;
        else if (d.toolsCheck) settings.tools[d.toolsCheck] = el.checked;
        else if (d.setting) settings[d.setting] = value;
        else if (d.defaultOp) {
          const op = d.defaultOp;
          if (d.key === "tool") {
            if (value === null) delete settings.tool_for[op];
            else settings.tool_for[op] = value;
          } else {
            const tolerance = { width: 0, length: 0, depth: 0, ...(settings.tolerances[op] || {}), [d.key]: value ?? 0 };
            if (tolerance.width || tolerance.length || tolerance.depth) settings.tolerances[op] = tolerance;
            else delete settings.tolerances[op];
          }
        }
        if (d.toolsCheck) render();
      });

      form.addEventListener("click", async (evt) => {
        const button = evt.target.closest("button");
        if (!button) return;
        if (button.dataset.contour) {
          settings.contour = button.dataset.contour;
          render();
          return;
        }
        const action = button.dataset.action;
        if (action === "cancel") dialog.close();
        else if (action === "import-too") form.querySelector('[data-file="too"]').click();
        else if (action === "load") form.querySelector('[data-file="settings"]').click();
        else if (action === "reset") {
          settings = structuredClone(hops.defaults);
          render();
        } else if (action === "save") {
          try {
            const target = await pickSaveTarget("hops_settings.json", "HOPS settings", JSON_TYPE);
            if (!target) return;
            const name = await writeSaveTarget(target, new Blob([JSON.stringify(settings, null, 2)], { type: "application/json" }));
            setMessage(`Saved the HOPS settings as ${name}.`);
          } catch (e) {
            setMessage(`Could not save the HOPS settings: ${e.message}`, "error");
          }
        } else if (action === "write") {
          // `write` asks where to save first, while this click still counts as a user gesture
          const ok = await write(async () => {
            const error = await postSettings(settings);
            if (error) throw new Error(error);
          });
          if (ok) {
            written = true;
            dialog.close();
          }
        }
      });
    });

    async function importToo(file) {
      const buffer = await file.arrayBuffer();
      const bytes = new Uint8Array(buffer);
      // tool tables are UTF-16 with a byte order mark, else Latin-1
      const utf16 = bytes.length > 1 && ((bytes[0] === 0xff && bytes[1] === 0xfe) || (bytes[0] === 0xfe && bytes[1] === 0xff));
      const text = new TextDecoder(utf16 ? (bytes[0] === 0xff ? "utf-16le" : "utf-16be") : "latin1").decode(buffer);
      const data = await (await fetch("api/hops-settings/too", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) })).json();
      if (!data.ok) {
        setMessage(data.error, "error");
        return;
      }
      const t = data.tools;
      if (!t.routers.length && !t.drills.length) {
        setMessage(`${file.name} has no loaded routers or drills: the tools were kept.`, "error");
        return;
      }
      // the table's tools, keeping the general settings (format, overcut, Clamex access holes)
      const keep = ["format_parts", "format_depth", "format_lead", "through_overcut", "clamex_access_holes"];
      settings.tools = { ...t, ...Object.fromEntries(keep.map((k) => [k, settings.tools[k]])) };
      setMessage(`Read ${t.routers.length} routers, ${t.drills.length} drills and ${t.drill_head_bits.length} drill head bits from ${file.name}.`);
    }
  });
}
