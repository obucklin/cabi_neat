import pytest

from cabi_neat import CabinetConfig
from cabi_neat.planner import Planner
from cabi_neat.planner import layout_to_data
from cabi_neat import resolve_layout


class StubApp:
    host = "127.0.0.1"
    websocket_port = 9001

    def __init__(self):
        self.routes = {}
        self.mounts = {}

    def add_route(self, path, endpoint, methods=("GET",)):
        self.routes[(path, tuple(methods))] = endpoint

    def add_static_mount(self, path, directory, name=None):
        self.mounts[path] = directory


class StubConfigurator:
    """Stands in for Configurator: applies configs without building geometry."""

    def __init__(self, config):
        self.app = StubApp()
        self.config = config
        self.version = 1
        self.last_error = None
        self.cabinet = None
        self.show_fronts = True
        self.open_fronts = False

    def set_display(self, show_fronts=None, open_fronts=None):
        if show_fronts is not None:
            self.show_fronts = show_fronts
        if open_fronts is not None:
            self.open_fronts = open_fronts

    stale = True

    def set_config(self, config):
        try:
            resolve_layout(config)
        except ValueError as e:
            self.last_error = str(e)
            return False
        self.config = config
        self.stale = True
        self.version += 1
        return True

    highlighted = set()
    picked = None
    pick_count = 0

    def set_highlight(self, names):
        self.highlighted = set(names)

    def generate(self):
        self.stale = False
        self.version += 1
        return []


@pytest.fixture
def planner():
    return Planner(StubConfigurator(CabinetConfig.shelves_and_drawers(2, 2)))


def test_routes_registered(planner):
    routes = planner.configurator.app.routes
    assert ("/planner/api/state", ("GET",)) in routes
    assert ("/planner/api/config", ("POST",)) in routes
    assert ("/planner/api/cutlist", ("GET",)) in routes
    assert ("/planner/api/display", ("POST",)) in routes
    assert ("/planner/api/generate", ("POST",)) in routes
    assert (planner.configurator.app.mounts["/planner"] / "index.html").exists()
    assert planner.url == "http://127.0.0.1:9001/planner/"


def test_state_is_sectioned(planner):
    state = planner.get_state()
    interior = state["config"]["interior"]
    # the single column of the columns shorthand is collapsed: the interior itself holds the drawers and the section
    assert interior["direction"] == "rows"
    kinds = [c.get("kind") or ("section" if c["one_door"] else "split") for c in interior["children"]]
    assert kinds == ["drawer", "drawer", "section"]
    assert state["display"] == {"show_fronts": True, "open_fronts": False}
    assert state["layout"]["regions"][""][2] == pytest.approx(800 - 36)
    assert any(p["category"] == "door" for p in state["layout"]["parts"])


def test_display_and_cut_list(planner):
    planner.post_display({"open_fronts": True})
    assert planner.configurator.open_fronts and planner.configurator.show_fronts
    assert planner.get_cut_list() == {"rows": []}
    assert planner.configurator.stale is False  # the cut list generated the model first


def test_config_changes_mark_the_3d_view_stale(planner):
    data = planner.get_state()["config"]
    result = planner.post_config(dict(data, width=900))
    assert result["ok"] and result["stale"] is True
    generated = planner.post_generate()
    assert generated["ok"] and generated["errors"] == [] and generated["stale"] is False


def test_post_config(planner):
    data = planner.get_state()["config"]
    data["width"] = 1000
    result = planner.post_config(data)
    assert result["ok"] and result["version"] == 2
    assert planner.configurator.config.width == 1000


def test_post_invalid_config(planner):
    data = planner.get_state()["config"]
    result = planner.post_config(dict(data, width=20))
    assert result == {"ok": False, "error": planner.configurator.last_error}
    assert planner.post_config({"bogus": 1})["ok"] is False


def test_layout_to_data_projects_parts():
    data = layout_to_data(resolve_layout(CabinetConfig.grid(2, 2)))
    shelf = next(p for p in data["parts"] if p["category"] == "shelf")
    assert set(shelf) == {"name", "category", "x", "z", "w", "h", "y", "d", "attributes"}
    assert shelf["attributes"]["split"] == "0" and shelf["attributes"]["gap"] == 0


def test_export_errors_are_readable(planner, monkeypatch):
    from cabi_neat import export

    def missing(*args, **kwargs):
        raise ValueError("no plates")

    planner.configurator.stale = False
    planner.configurator.cabinet = object()
    monkeypatch.setattr(export, "hops_zip", missing)
    response = planner.get_hops()
    assert response.status_code == 500
    assert b"HOPS export failed: ValueError: no plates" in response.body


def test_new_cabinet(planner):
    from cabi_neat.config import Cell

    data = planner.get_new()
    config = CabinetConfig.from_data(data)
    assert isinstance(config.interior, Cell) and config.interior.door_style.value == "none"
    assert (config.width, config.height, config.depth) == (CabinetConfig().width, CabinetConfig().height, CabinetConfig().depth)
    result = planner.post_config(data)
    assert result["ok"]
    parts = {p["category"] for p in result["layout"]["parts"]}
    assert parts == {"side", "top", "bottom", "back"}  # just the carcass


def test_highlight_and_pick(planner):
    planner.post_highlight({"parts": ["shelf_0_0"]})
    assert planner.get_version()["highlight"] == ["shelf_0_0"]
    planner.configurator.picked, planner.configurator.pick_count = "shelf_0_1", 3
    assert planner.get_version()["pick"] == {"name": "shelf_0_1", "count": 3}
    door = next(p for p in planner.get_state()["layout"]["parts"] if p["category"] == "door")
    assert len(door["attributes"]["hinges"]) == 4 and door["attributes"]["cup_diameter"] == 35
