import re

import pytest

pytest.importorskip("OCP", reason="requires the OCC backend (compas_brep[occ])")

from compas_timber.btlx import BTLxReader  # noqa: E402

from cabi_neat import Cabinet  # noqa: E402
from cabi_neat import CabinetConfig  # noqa: E402
from cabi_neat import export  # noqa: E402

pytestmark = pytest.mark.requires_occ


@pytest.fixture(scope="module")
def cabinet():
    return Cabinet(CabinetConfig.stack(["drawer", "drawer", "open", "open"], drawer_front_one_piece=True))


def test_model_is_in_millimetres(cabinet):
    assert cabinet.model.tolerance.unit == "MM"


def test_btlx(cabinet, tmp_path):
    path = tmp_path / "cabinet.btlx"
    before = {name: list(p.features) for name, p in cabinet.plates.items()}
    xml, report = export.btlx(cabinet, str(path))
    assert xml.count("<Part ") == len(cabinet.plates)
    # dimensions in mm, not scaled by 1000
    side = re.search(r'<Part [^>]*Annotation="side_left"[^>]*>', xml).group(0)
    assert 'Width="1200.000"' in side and 'Height="18.000"' in side
    # Clamex grooves aren't BTLx processings: reported, not exported, and still on the plates afterwards
    assert report.skipped and all("Clamex P-14" in s for s in report.skipped)
    assert "P14" not in xml
    assert {name: list(p.features) for name, p in cabinet.plates.items()} == before
    # readable by compas_timber, with every BTLx processing (the reader adds an outline of its own to every part)
    model = BTLxReader().read(str(path))
    exported = sum(1 for p in cabinet.plates.values() for f in p.features if type(f).__name__ in ("FreeContour", "Drilling"))
    assert xml.count("<FreeContour") + xml.count("<Drilling") == exported
    assert sum(len(e.features) for e in model.elements()) == exported + len(cabinet.plates)


def test_hops(cabinet, tmp_path):
    pytest.importorskip("easyhops")
    files, report = export.hops(cabinet, str(tmp_path))
    assert report.errors == []
    assert set(files) == {"{}.hop".format(name) for name in cabinet.plates}
    assert len(list(tmp_path.glob("*.hop"))) == len(cabinet.plates)
    door = next(text for name, text in files.items() if name.startswith("door"))
    # the hinge pots (pockets 13 deep) and screw holes (D3, 11 deep)
    assert door.count("TIEFE:=-13") == 4
    assert door.count("HorzB") == 8 and "3,-11.000" in door
    # part size from the blank, e.g. a drawer bottom including its tongues in the grooves
    bottom = files["drawer_0_0_bottom.hop"]
    part = cabinet.layout.part("drawer_0_0_bottom")
    assert re.search(r"DY := ([\d.]+)", bottom).group(1) == "{:.1f}".format(part.size[1] + 2 * 9)
    # a panel machined only from one face needs no turning over
    assert "Turn the panel over" not in files["side_left.hop"]
    # left out: Clamex grooves and dowel holes in the drawer sides' ends
    kinds = {s.split(": ", 1)[1].split(" (")[0].split(" -")[0] for s in report.skipped}
    assert kinds == {"Clamex P-14 groove", "Drilling into an end"}


def test_hops_zip(cabinet):
    pytest.importorskip("easyhops")
    import io
    import zipfile

    data, report = export.hops_zip(cabinet)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "report.txt" in names and len(names) == len(cabinet.plates) + 1


def test_hops_turns_panels_machined_from_both_faces(tmp_path):
    pytest.importorskip("easyhops")
    # a divider with shelves on both sides has mortises in both faces: one face first, then a stop to turn it over
    cabinet = Cabinet(CabinetConfig.grid(2, 2, door_style="none"))
    files, report = export.hops(cabinet)
    divider = files["divider_0.hop"]
    assert divider.count("Turn the panel over") == 1
    before, after = divider.split("Turn the panel over")
    # the mortises of the shelves on either side (4.5 deep); the top face also has the gaps between its own tenons
    assert before.count("TIEFE:=-4.500") == after.count("TIEFE:=-4.500") == 2
    assert after.count("TIEFE:=-18") == 6
