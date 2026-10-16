"""The planner without a server, for the browser: the GitHub Pages site runs this with Pyodide.

:class:`WebPlanner` keeps the cabinet like :class:`cabi_neat.configurator.Configurator` does, and answers the planner
page's API calls (:meth:`WebPlanner.request`) with the same :class:`cabi_neat.planner.Planner` handlers. There's no
compas_threejs viewer and no Brep backend in the browser: :meth:`WebPlanner.scene` gives each part as its silhouette
(:func:`cabi_neat.dxf.plate_shape`) and thickness, for the page's own 3D view to extrude.
"""

import inspect
import json

from .builder import Cabinet
from .builder import open_transformation
from .config import CabinetConfig
from .dxf import plate_shape
from .hops import HopsSettings
from .layout import part_category_group
from .layout import resolve_layout
from .planner import Planner
from .planner import Response
from .sections import sectioned


class _App:
    """Collects the planner's routes, so :meth:`WebPlanner.request` can call them."""

    host = "localhost"
    websocket_port = 0

    def __init__(self):
        self.routes = {}

    def add_route(self, path, endpoint, methods=("GET",)):
        for method in methods:
            self.routes[(method, path)] = endpoint

    def add_static_mount(self, path, directory, name=None):
        pass


class WebPlanner:
    """The cabinet being planned, and the planner's API on it (see :class:`cabi_neat.planner.Planner`).

    Parameters
    ----------
    config : :class:`CabinetConfig`, optional
    """

    def __init__(self, config=None):
        self.app = _App()
        config = config or CabinetConfig.shelves_and_drawers(2, 2)
        try:
            config = sectioned(config)  # the form the planner edits, so parts have the same names in the plan and in 3D
        except ValueError:
            pass
        self.config = config
        self.cabinet = None
        self.version = 0
        self.last_error = None
        self.stale = True
        self.show_fronts = True
        self.open_fronts = False
        self.highlighted = set()
        self.picked = None
        self.pick_count = 0
        self.hops_settings = HopsSettings()
        self.revision = 0  # of what the 3D view shows: the cabinet and the display options
        self._shapes = {}  # part name -> the plate's silhouette, of the current cabinet
        self.planner = Planner(self)

    # -- what the planner calls (see cabi_neat.configurator.Configurator) -------------------

    def set_config(self, config):
        try:
            resolve_layout(config)
        except ValueError as e:
            self.last_error = str(e)
            return False
        self.config = config
        self.last_error = None
        self.stale = True
        self.version += 1
        return True

    def generate(self):
        try:
            cabinet = Cabinet(self.config)
        except ValueError as e:
            self.last_error = str(e)
            return None
        self.cabinet = cabinet
        self.last_error = None
        self.stale = False
        self.version += 1
        self.revision += 1
        self._shapes = {}
        return list(cabinet.errors)

    def set_display(self, show_fronts=None, open_fronts=None):
        if show_fronts is not None:
            self.show_fronts = bool(show_fronts)
        if open_fronts is not None:
            self.open_fronts = bool(open_fronts)
        self.revision += 1

    def set_highlight(self, names):
        self.highlighted = set(names)

    # -- the page's calls --------------------------------------------------------------------

    def request(self, method, path, body=None):
        """Answer one of the planner page's API calls: ``path`` like ``"api/state"``, ``body`` the JSON text posted.

        Returns
        -------
        dict
            ``{"status", "json"}`` for JSON, or ``{"status", "body" (bytes), "media_type", "headers"}`` (header names in lower case).
        """
        path = "/planner/" + path.split("?")[0].lstrip("/")
        handler = self.app.routes.get((method.upper(), path))
        if handler is None:
            return {"status": 404, "json": {"ok": False, "error": "No such route: {} {}".format(method, path)}}
        takes_body = bool(inspect.signature(handler).parameters)  # e.g. posting a config; generating takes none
        result = handler(json.loads(body or "{}")) if takes_body else handler()
        if isinstance(result, Response):
            return {"status": result.status_code, "body": result.body, "media_type": result.media_type, "headers": {k.lower(): v for k, v in dict(result.headers).items()}}
        return {"status": 200, "json": result}

    def request_json(self, method, path, body=None):
        """:meth:`request`, for JavaScript: everything as JSON text, bodies as Latin-1 text of their bytes."""
        answer = self.request(method, path, body)
        if "body" in answer:
            answer = dict(answer, body=answer["body"].decode("latin-1"))
        return json.dumps(answer)

    def view_state(self):
        """What the 3D view should show, as JSON text: ``{"revision", "highlight"}`` (see :meth:`scene`)."""
        return json.dumps({"revision": self.revision, "highlight": sorted(self.highlighted)})

    def pick(self, name):
        """A part was clicked in the 3D view (see the planner's ``api/version``)."""
        self.picked = name
        self.pick_count += 1

    def scene(self):
        """The parts as the 3D view shows them, as JSON text: ``{"revision", "parts": [...]}``.

        Each part: ``name``, ``group`` (``"carcass"``, ``"front"``, ``"drawer_box"``), ``frame`` (``origin``,
        ``xaxis``, ``yaxis``, ``zaxis``), ``polygons`` (the silhouette in the frame's xy: ``[exterior, *holes]`` each
        ``[[x, y], ...]``), ``z0`` and ``z1`` (along the frame's z, where its faces are) and ``matrix`` (where it's
        moved to when fronts are shown open, 4x4 rows, or None). Fronts are left out when they're hidden.
        """
        parts = []
        if self.cabinet is not None:
            for part in self.cabinet.layout.parts:
                group = part_category_group(part.category)
                if group == "front" and not self.show_fronts:
                    continue
                plate = self.cabinet.plates[part.name]
                parts.append(dict(self._part(part.name, plate), group=group, matrix=self._matrix(part)))
        return json.dumps({"revision": self.revision, "parts": parts})

    def _matrix(self, part):
        if not self.open_fronts:
            return None
        moved = open_transformation(part)
        return [list(row) for row in moved.matrix] if moved else None

    def _part(self, name, plate):
        if name not in self._shapes:
            frame = plate.frame
            origin, zaxis = list(frame.point), list(frame.zaxis)

            def height(point):
                return sum((p - o) * z for p, o, z in zip(point, origin, zaxis))

            shape = plate_shape(plate)
            pieces = list(shape.geoms) if shape.geom_type == "MultiPolygon" else [shape]
            polygons = [[_ring(piece.exterior)] + [_ring(hole) for hole in piece.interiors] for piece in pieces if not piece.is_empty]
            self._shapes[name] = {
                "name": name,
                "frame": {"origin": origin, "xaxis": list(frame.xaxis), "yaxis": list(frame.yaxis), "zaxis": zaxis},
                "polygons": polygons,
                "z0": height(plate.outline_a.points[0]),
                "z1": height(plate.outline_b.points[0]),
            }
        return self._shapes[name]


def _ring(ring):
    return [[round(x, 3), round(y, 3)] for x, y in list(ring.coords)[:-1]]
