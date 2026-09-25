"""Browser configurator: the 2D planner page plus a compas_threejs 3D view of the cabinet.

Run with ``python -m cabi_neat`` (or the ``cabi-neat`` script).
"""

import threading
import webbrowser

from compas.colors import Color
from compas.geometry import Scale
from compas_threejs.materials import Material
from compas_threejs.viewer import App

from .builder import Cabinet
from .builder import open_transformation
from .config import CabinetConfig
from .layout import part_category_group
from .planner import Planner

MATERIALS = {
    "carcass": Material(color=Color.from_hex("#d9b98c"), roughness=0.8),
    "front": Material(color=Color.from_hex("#f2efe9"), roughness=0.6),
    "drawer_box": Material(color=Color.from_hex("#c49a6c"), roughness=0.8),
}

# the model is in millimetres, the viewer's default camera and lights are set up for metres
DISPLAY_SCALE = Scale.from_factors([0.001, 0.001, 0.001])


class Configurator:
    """Holds the current cabinet, shows it in the 3D view, and serves the planner page that edits it.

    Parameters
    ----------
    config : :class:`CabinetConfig`, optional
        Initial configuration.
    app : :class:`compas_threejs.viewer.App`, optional
    """

    def __init__(self, config=None, app=None):
        self.app = app or App()
        self.app.default_lighting = True
        self.config = config or CabinetConfig.shelves_and_drawers(2, 2)
        self.cabinet = None
        self.show_fronts = True
        self.open_fronts = False
        self._objects = []
        self._lock = threading.Lock()
        self.version = 0  # incremented on every applied config, so the planner page can notice changes
        self.last_error = None
        self.planner = Planner(self)

    def apply(self, config):
        """Build ``config`` and show it. Invalid configs are reported and the previous cabinet is kept."""
        with self._lock:
            try:
                cabinet = Cabinet(config)
            except ValueError as e:
                self.last_error = str(e)
                print("Invalid configuration, keeping the previous one: {}".format(e))
                return False
            self.config = config
            self.cabinet = cabinet
            self.last_error = None
            self.version += 1
            for error in cabinet.errors:
                print("Joinery: {}".format(error))
            self._redraw()
            return True

    def set_display(self, show_fronts=None, open_fronts=None):
        """Change what the 3D view shows: fronts hidden, and/or fronts opened (doors swung, drawers pulled out)."""
        with self._lock:
            if show_fronts is not None:
                self.show_fronts = bool(show_fronts)
            if open_fronts is not None:
                self.open_fronts = bool(open_fronts)
            self._redraw()

    def _redraw(self):
        self.app.start_spinner("Building cabinet")
        try:
            for obj in self._objects:
                self.app.remove_object(obj)
            self._objects = []
            for part in self.cabinet.layout.parts:
                group = part_category_group(part.category)
                if group == "front" and not self.show_fronts:
                    continue
                transformation = DISPLAY_SCALE
                if self.open_fronts:
                    moved = open_transformation(part)
                    if moved:
                        transformation = DISPLAY_SCALE * moved
                geometry = self.cabinet.plates[part.name].modelgeometry.transformed(transformation)
                self.app.add_geometry(geometry, MATERIALS[group])
                self._objects.append(geometry)
        finally:
            self.app.stop_spinner()

    def start(self, show=True):
        """Start the server and, with ``show``, open the planner in the browser."""
        if not self.apply(self.config):
            raise ValueError("The initial configuration is invalid: {}".format(self.last_error))
        c = self.config
        center = [0.0005 * c.width, 0.0005 * c.depth, 0.0005 * c.height]
        size = 0.001 * max(c.width, c.height)
        self.app.set_view([center[0] + 1.2 * size, center[1] - 2.0 * size, center[2] + 0.8 * size], target=center)
        print("Planner: {}".format(self.planner.url))
        if show:

            def open_planner(i):
                if i == 0:
                    webbrowser.open(self.planner.url)

            self.app.loop = open_planner
        self.app.start(show=False)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Cabinet planner")
    parser.add_argument("config", nargs="?", help="Optional cabinet config JSON file to start from.")
    parser.add_argument("--no-browser", action="store_true", help="Don't open a browser window.")
    parser.add_argument("--port", type=int, default=9001, help="Server port.")
    parser.add_argument("--frontend-dir", help="Built compas_threejs frontend to serve, if the installed package has none.")
    args = parser.parse_args()
    config = CabinetConfig.from_json(args.config) if args.config else None
    app = App(websocket_port=args.port, frontend_dir=args.frontend_dir)
    Configurator(config, app=app).start(show=not args.no_browser)


if __name__ == "__main__":
    main()
