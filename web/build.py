"""Build the static site (GitHub Pages): the planner page, with its Python running in the browser.

    python web/build.py _site
    python -m http.server -d _site          # then open http://localhost:8000

The site is the planner's page (``src/cabi_neat/planner_frontend``) plus ``web/``:

- ``index.html`` is the planner's, with ``web.js`` loaded first (which answers the page's API calls in the browser,
  see :mod:`cabi_neat.web`), a 3D view of its own instead of the compas_threejs viewer, and a loading screen.
- ``wheels/`` has the Python packages the browser installs with Pyodide: cabi_neat (built from this checkout),
  compas_timber (built from ``COMPAS_TIMBER``), and compas, compas_model and compas_brep from PyPI. ``manifest.json``
  lists them. numpy, scipy and shapely come with Pyodide.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "src" / "cabi_neat" / "planner_frontend"
WEB = Path(__file__).resolve().parent

# compas_timber from its repository: the planner follows its main branch, which PyPI's releases lag behind
COMPAS_TIMBER = "git+https://github.com/gramaziokohler/compas_timber@773a82805485d660b4b56cdec9b30db7709023f7"
# pure Python packages from PyPI, at the versions the planner is tested with
PYPI = ["compas==2.15.1", "compas_model==0.9.2", "compas_brep==0.4.0"]

# three.js for the 3D view, from a CDN (web/viewer3d.js imports "three" and "three/addons/...")
IMPORT_MAP = """<script type="importmap">
      {"imports": {"three": "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js", "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/"}}
    </script>"""

BOOT = """<div id="boot" class="boot" role="status" aria-live="polite">
      <div class="boot-box">
        <strong>Loading the cabinet planner</strong>
        <span id="boot-status">Starting…</span>
        <span class="boot-slow">The first visit downloads Python and its packages (about 30 MB) and takes a little while; later visits are faster.</span>
      </div>
    </div>"""


def pip(*args):
    subprocess.run([sys.executable, "-m", "pip", *args], check=True)


def build_wheels(out):
    out.mkdir(parents=True, exist_ok=True)
    pip("wheel", "--no-deps", "-q", "-w", str(out), str(ROOT), COMPAS_TIMBER)
    pip("download", "--no-deps", "--only-binary=:all:", "-q", "-d", str(out), *PYPI)
    wheels = sorted(p.name for p in out.glob("*.whl"))
    (out / "manifest.json").write_text(json.dumps({"wheels": wheels}, indent=2))
    return wheels


def page(html):
    """The planner's index.html for the static site."""

    def replace(old, new):
        nonlocal html
        if old not in html:
            raise SystemExit("index.html changed: can't find {!r}".format(old))
        html = html.replace(old, new)

    replace('<link rel="stylesheet" href="style.css" />', '<link rel="stylesheet" href="style.css" />\n    <link rel="stylesheet" href="web.css" />\n    ' + IMPORT_MAP)
    replace('<iframe id="view3d" title="3D view"></iframe>', '<div id="view3d" class="view3d" title="3D view"></div>')
    replace('<script type="module" src="planner.js"></script>', '<script type="module" src="web.js"></script>\n    <script type="module" src="planner.js"></script>')
    replace("<body>", "<body>\n    " + BOOT)
    return html


def main():
    site = Path(sys.argv[1] if len(sys.argv) > 1 else "_site").resolve()
    if site.exists():
        shutil.rmtree(site)
    shutil.copytree(FRONTEND, site, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("web.js", "pyworker.js", "viewer3d.js", "web.css"):
        shutil.copy(WEB / name, site / name)
    (site / "index.html").write_text(page((FRONTEND / "index.html").read_text(encoding="utf-8")), encoding="utf-8")
    (site / ".nojekyll").write_text("")
    with tempfile.TemporaryDirectory() as tmp:
        wheels = build_wheels(Path(tmp))
        shutil.copytree(tmp, site / "wheels")
    print("Built {} with {}".format(site, ", ".join(wheels)))


if __name__ == "__main__":
    main()
