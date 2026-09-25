"""2D front-elevation planner, served as an extra page on the configurator's App.

The page (``planner_frontend/``) edits the interior tree in section form (see :mod:`cabi_neat.sections`) and posts
the whole config back; the configurator rebuilds the 3D model, which the page shows in an embedded viewer.
"""

from pathlib import Path

from fastapi import Body

from .config import CabinetConfig
from .layout import resolve_layout
from .sections import sectioned

FRONTEND_DIR = Path(__file__).parent / "planner_frontend"

_PART_ATTRIBUTES = ("split", "gap", "hinge_side", "drawer")


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
    return {"parts": parts, "regions": {k: list(v) for k, v in layout.regions.items()}}


class Planner:
    """Registers the planner page and its API on a :class:`Configurator`'s App. Must be created before the App starts.

    Routes
    ------
    ``GET /planner/``
        The planner page.
    ``GET /planner/api/state``
        ``{"version", "config", "layout", "error"}`` for the current config, with ``config.interior`` in section form.
    ``POST /planner/api/config``
        Apply a config. Returns the new state with ``"ok": true``, or ``{"ok": false, "error": ...}``.
    ``GET /planner/api/version``
        ``{"version"}``, which increments whenever the configurator applies a config.
    ``GET /planner/api/cutlist``
        ``{"rows": [[category, name, length, width, thickness], ...]}`` for the current cabinet.
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
        app.add_route("/planner/api/display", self.post_display, methods=("POST",))
        app.add_static_mount("/planner", FRONTEND_DIR, name="planner_frontend")

    @property
    def url(self):
        app = self.configurator.app
        return "http://{}:{}/planner/".format(app.host, app.websocket_port)

    def get_version(self):
        return {"version": self.configurator.version}

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
        }

    def get_cut_list(self):
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
        if not self.configurator.apply(config):
            return {"ok": False, "error": self.configurator.last_error}
        return dict(self.get_state(), ok=True)
