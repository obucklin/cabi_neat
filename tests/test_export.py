import re

import pytest

pytest.importorskip("OCP", reason="requires the OCC backend (compas_brep[occ])")

from compas_timber.btlx import BTLxReader  # noqa: E402

from cabi_neat import Cabinet  # noqa: E402
from cabi_neat import CabinetConfig  # noqa: E402
from cabi_neat import export  # noqa: E402
from cabi_neat.hops import HopsTools  # noqa: E402
from cabi_neat.hops import Router  # noqa: E402

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
    files, report = export.hops(cabinet, str(tmp_path))
    assert report.errors == []
    assert set(files) == {"{}.hop".format(name) for name in cabinet.plates}
    # written like the machine's own files: Windows line endings
    written = (tmp_path / "side_left.hop").read_bytes()
    assert written.startswith(b";MAKROTYP=0\r\n") and b"\n" not in written.replace(b"\r\n", b"")
    for name, text in files.items():
        assert "Fertigteil (DX,DY,DZ,0,0,0,0,0,'',0,0,0)" in text, name
        assert "CALL Park_V7 ( VAL MODE:=3,POSX:=0,POSY:=0)" in text, name
    # part size from the blank, e.g. a drawer bottom including its tongues in the grooves
    part = cabinet.layout.part("drawer_0_0_bottom")
    assert re.search(r"DY := ([\d.]+)", files["drawer_0_0_bottom.hop"]).group(1) == "{:g}".format(part.size[1] + 2 * 9)
    assert report.skipped == []


def test_hops_hinges(cabinet):
    door = export.hops(cabinet)[0]["door_0_2.hop"]
    # pots milled with the D20 router on a circle, 13 deep, as in the earlier programs
    assert "WZF (403," in door
    assert door.count("CALL _Kreisbahn_V5") == 4 and "RADIUS:=17.5" in door and "TIEFE:=13," in door
    # the screw holes with the D3 drill, the mounting plates' D5 holes in the side with the short D5: always the spindle
    assert "WZB (602," in door and door.count("Bohrung (") == 8 and ",3,-11,10," in door
    side = export.hops(cabinet)[0]["side_left.hop"]
    assert "WZB (609," in side and side.count("Bohrung (37,") == 8


def test_hops_clamex(cabinet):
    files, _ = export.hops(cabinet)
    # the edge halves with the disc (108) and access holes drilled with 135: three per end of the top
    top = files["top.hop"]
    assert top.count("CALL Clamex1_Stumpf (") == 2
    assert "CLAMEX_ID_H:=108" in top and "CLAMEX_B_ID:=135" in top and "ART:=0" in top
    left = next(line for line in top.splitlines() if "C1:=-1" in line)
    assert "EY1:=60" in left and "EY3:=60" in left and "C2:=-1" in left and "C4:=0" in left
    # the face halves with the finger mill, along the joint
    side = files["side_left.hop"]
    assert side.count("CALL Clamex_MS_FI") == 6 and "CL_WKZ:=136" in side and "DW:=0," in side
    # one connector per drawer side, in the middle of its end, and the dowel holes next to it
    drawer_side = files["drawer_0_0_side_l.hop"]
    assert "C2:=-1" in drawer_side and drawer_side.count("HorzB (0,") == 2 and "WZB (603," in drawer_side
    no_holes = export.hops(cabinet, tools=HopsTools(clamex_access_holes=False))[0]["top.hop"]
    assert "ART:=1" in no_holes


def test_hops_turns_panels_machined_from_both_faces():
    # a divider with shelves on both sides has mortises in both faces: one face first, then a stop to turn it over
    cabinet = Cabinet(CabinetConfig.grid(2, 2, door_style="none"))
    files, report = export.hops(cabinet)
    divider = files["divider_0.hop"]
    assert divider.count("CALL MachineStop_V7") == 1
    before, after = divider.split("CALL MachineStop_V7")
    # formatted once, in the first pass, so the second is placed on the finished part
    assert before.count("_Sformat_V5") == 1 and "_Sformat_V5" not in after
    assert before.count("TIEFE:=-4.5,") == after.count("TIEFE:=-4.5,") == 2


def test_hops_tools():
    tools = HopsTools()
    # pockets: the largest router that fits
    assert tools.router_for(18, 5).number == 402
    assert tools.router_for(8, 9).number == 410
    assert tools.router_for(30, 9).number == 403
    assert tools.router_for(4, 9) is None
    # drillings: the shortest drill of that diameter that's long enough
    assert tools.drill_for(5, 12).number == 609
    assert tools.drill_for(4, 30).number == 607
    assert tools.drill_for(8, 20).number == 603
    assert tools.drill_for(5, 40) is None
    assert tools.drill_for(7, 10) is None


def test_hops_tools_from_tool_table(tmp_path):
    table = """[ToolsCount]
Tools=4
[ToolData0]
Name=Turbex D20
ToolNo=23
GroupName=Schaft
[ToolData0CuttingEdge0]
ID=403
Radius=10
UseableLength=95
[ToolData1]
Name=Drill D8
ToolNo=-1
GroupName=Drill
[ToolData1CuttingEdge0]
ID=603
Radius=4
UseableLength=80
[ToolData2]
Name=35 mm
ToolNo=23
GroupName=Bohrkopf
[ToolData2CuttingEdge0]
ID=-1
Radius=17.5
UseableLength=50
DrillingType=3
Description=Topfbohrer
[ToolData3]
Name=Saw blade
ToolNo=17
GroupName=Saw blades
[ToolData3CuttingEdge0]
ID=202
Radius=175
UseableLength=110
"""
    path = tmp_path / "tools.too"
    path.write_bytes(table.encode("utf-16"))
    tools = HopsTools.from_too(str(path), clamex_disc=109)
    assert tools.routers == (Router(403, 20.0, 95.0, "Turbex D20"),)
    assert tools.drills == ()  # not in the tool changer
    assert tools.saw == 202 and tools.clamex_disc == 109


def test_hops_rabbets_are_single_lines(cabinet):
    # as in the earlier programs: one line along the rabbet's inner edge, radius correction 1, over the whole part
    side = export.hops(cabinet)[0]["side_left.hop"]
    assert "; Rabbet 8 wide, 9 deep" in side
    rabbet = side[side.index("; Rabbet") :].splitlines()
    assert rabbet[1] == "WZF (403,_VE,_V,_VA,_SD,_ANF,'1')"
    assert rabbet[2] == "SP (492,1200,9,1,1,_ANF*2,0,0,0,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0.5mm)"  # Z: the floor above the underside
    assert rabbet[3:5] == ["G01 (0,-DY,0,0,10,2)", "EP (1,_ANF*2,0)"]
    assert "TASCHENBREITE:=1252" not in side
