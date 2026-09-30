"""2D front-elevation planner, served as an extra page on the configurator's App.

The page (``planner_frontend/``) edits the interior tree in section form (see :mod:`cabi_neat.sections`) and posts
the whole config back; the configurator rebuilds the 3D model, which the page shows in an embedded viewer.
"""

from pathlib import Path

from fastapi import Body
from fastapi import Response

from .config import CabinetConfig
from .config import SHELF_HOLE_DEFAULTS
from .config import Cell
from .joints import CupHingeJoint
from .layout import resolve_layout
from .sections import sectioned

FRONTEND_DIR = Path(__file__).parent / "planner_frontend"

_PART_ATTRIBUTES = ("split", "gap", "hinge_side", "drawer", "joint", "joint_parameters", "hinges", "cup_diameter", "node")


def layout_to_data(layout):
    """JSON-friendly front elevation of a layout: parts projected onto the xz plane, plus node regions."""
    parts = []
    for p in layout.parts:
        parts.append(
            {
                "name": p.name,
                "category": p.category,
                "x": p.origin[0],
                "z": p.origin[2],
                "w": p.size[0],
                "h": p.size[2],
                "y": p.origin[1],
                "d": p.size[1],
                "attributes": {k: v for k, v in p.attributes.items() if k in _PART_ATTRIBUTES},
            }
        )
    # shelf holes projected onto the front: (x of the panel face, z, direction into the panel along x, depth), once per height
    holes = sorted({(h.point[0], h.point[2], h.direction[0], h.depth) for h in layout.holes})
    return {"parts": parts, "regions": {k: list(v) for k, v in layout.regions.items()}, "holes": [list(h) for h in holes]}


def _export_error(kind, error, message=None):
    """A plain-text 500 response saying why an export failed, also printed in the server's console."""
    import traceback

    traceback.print_exc()
    text = message or "{} export failed: {}: {}".format(kind, type(error).__name__, error)
    return Response(content=text, status_code=500, media_type="text/plain")


def _download_headers(filename, report):
    from .export import _short_summary

    summary = _short_summary(report).encode("ascii", "replace").decode("ascii")
    return {"Content-Disposition": 'attachment; filename="{}"'.format(filename), "X-Export-Summary": summary, "Access-Control-Expose-Headers": "X-Export-Summary"}


def new_config():
    """A fresh cabinet: the default dimensions and settings, and an empty interior (no shelves, drawers or door)."""
    return CabinetConfig(interior=Cell(door_style="none"))


class Planner:
    """Registers the planner page and its API on a :class:`Configurator`'s App. Must be created before the App starts.

    Routes
    ------
    ``GET /planner/``
        The planner page.
    ``GET /planner/api/state``
        ``{"version", "config", "layout", "error"}`` for the current config, with ``config.interior`` in section form.
    ``POST /planner/api/config``
        Make a config the current one (validated, not built). Returns the new state with ``"ok": true``, or
        ``{"ok": false, "error": ...}``.
    ``POST /planner/api/generate``
        Build the current config and show it in the 3D view: ``{"ok", "errors"}`` plus the new state.
    ``GET /planner/api/version``
        ``{"version", "pick": {"name", "count"}}``: the version increments whenever the configurator applies a config;
        the pick is the part last clicked in the 3D view.
    ``POST /planner/api/highlight``
        ``{"parts": [names]}``: highlight these parts in the 3D view.
    ``GET /planner/api/cutlist``
        ``{"rows": [[category, name, length, width, thickness], ...]}`` for the current cabinet.
    ``GET /planner/api/new``
        A fresh cabinet's config: default dimensions and settings, and an empty interior.
    ``GET /planner/api/export/btlx``, ``GET /planner/api/export/hops``, ``GET /planner/api/export/dxf``
        The cabinet as a BTLx file, its HOPS jobs as a zip, or its part outlines as a DXF (generating the model first if needed). The header
        ``X-Export-Summary`` says what couldn't be exported.
    ``POST /planner/api/display``
        ``{"show_fronts", "open_fronts"}`` (either optional): what the 3D view shows.
    """

    def __init__(self, configurator):
        self.configurator = configurator
        app = configurator.app
        app.add_route("/planner/api/state", self.get_state)
        app.add_route("/planner/api/version", self.get_version)
        app.add_route("/planner/api/config", self.post_config, methods=("POST",))
        app.add_route("/planner/api/cutlist", self.get_cut_list)
        app.add_route("/planner/api/generate", self.post_generate, methods=("POST",))
        app.add_route("/planner/api/new", self.get_new)
        app.add_route("/planner/api/highlight", self.post_highlight, methods=("POST",))
        app.add_route("/planner/api/export/btlx", self.get_btlx)
        app.add_route("/planner/api/export/hops", self.get_hops)
        app.add_route("/planner/api/export/dxf", self.get_dxf)
        app.add_route("/planner/api/display", self.post_display, methods=("POST",))
        app.add_static_mount("/planner", FRONTEND_DIR, name="planner_frontend")

    @property
    def url(self):
        app = self.configurator.app
        return "http://{}:{}/planner/".format(app.host, app.websocket_port)

    def get_version(self):
        c = self.configurator
        return {
            "version": c.version,
            "pick": {"name": getattr(c, "picked", None), "count": getattr(c, "pick_count", 0)},
            "highlight": sorted(getattr(c, "highlighted", ())),
        }

    def post_highlight(self, data: dict = Body(...)):
        self.configurator.set_highlight(data.get("parts") or [])
        return {"ok": True}

    def get_state(self):
        config = self.configurator.config
        try:
            config = sectioned(config)
        except ValueError:
            pass
        return {
            "version": self.configurator.version,
            "config": config.to_data(),
            "layout": layout_to_data(resolve_layout(config)),
            "error": self.configurator.last_error,
            "display": {"show_fronts": self.configurator.show_fronts, "open_fronts": self.configurator.open_fronts},
            "stale": self.configurator.stale,
            "joint_defaults": config.joint_defaults(),
            "hinge_defaults": {key: CupHingeJoint.PARAMETERS[key] for key in CupHingeJoint.DIMENSIONS},
            "shelf_hole_defaults": SHELF_HOLE_DEFAULTS,
        }

    def post_generate(self):
        errors = self.configurator.generate()
        if errors is None:
            return {"ok": False, "error": self.configurator.last_error}
        return dict(self.get_state(), ok=True, errors=[str(getattr(e, "debug_info", e)) for e in errors])

    def get_new(self):
        return new_config().to_data()

    def _built_cabinet(self):
        if self.configurator.stale:
            self.configurator.generate()
        return self.configurator.cabinet

    def get_btlx(self):
        from . import export

        try:
            xml, report = export.btlx(self._built_cabinet())
        except Exception as e:
            return _export_error("BTLx", e)
        return Response(content=xml, media_type="application/xml", headers=_download_headers("cabinet.btlx", report))

    def get_hops(self):
        from . import export

        try:
            data, report = export.hops_zip(self._built_cabinet())
        except Exception as e:
            return _export_error("HOPS", e)
        return Response(content=data, media_type="application/zip", headers=_download_headers("cabinet_hops.zip", report))

    def get_dxf(self):
        from . import export
        from .dxf import dxf_bytes

        try:
            text, report = export.dxf(self._built_cabinet())
        except Exception as e:
            return _export_error("DXF", e)
        return Response(content=dxf_bytes(text), media_type="application/dxf", headers=_download_headers("cabinet_outlines.dxf", report))

    def get_cut_list(self):
        # the cut list is measured on the built plates
        if self.configurator.stale:
            self.configurator.generate()
        cabinet = self.configurator.cabinet
        return {"rows": [list(row) for row in cabinet.cut_list()] if cabinet else []}

    def post_display(self, data: dict = Body(...)):
        self.configurator.set_display(data.get("show_fronts"), data.get("open_fronts"))
        return {"ok": True}

    def post_config(self, data: dict = Body(...)):
        try:
            config = CabinetConfig.from_data(data)
        except (TypeError, ValueError) as e:
            return {"ok": False, "error": str(e)}
        if not self.configurator.set_config(config):
            return {"ok": False, "error": self.configurator.last_error}
        return dict(self.get_state(), ok=True)
