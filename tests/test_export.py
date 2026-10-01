import re

import pytest

pytest.importorskip("OCP", reason="requires the OCC backend (compas_brep[occ])")

from compas_timber.btlx import BTLxReader  # noqa: E402

from cabi_neat import Cabinet  # noqa: E402
from cabi_neat import CabinetConfig  # noqa: E402
from cabi_neat import export  # noqa: E402
from cabi_neat.hops import Drill  # noqa: E402
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
    # every plate has a program, identical ones share it
    names = {"{}.hop".format(name) for name in cabinet.plates}
    covered = {n for n in files if n in names} | {p + ".hop" for parts in report.copies.values() for p in parts}
    assert covered == names
    # written like the machine's own files: Windows line endings
    written = (tmp_path / "side_left.hop").read_bytes()
    assert written.startswith(b";MAKROTYP=0\r\n") and b"\n" not in written.replace(b"\r\n", b"")
    for name, text in files.items():
        assert "Fertigteil (DX,DY,DZ,0,0,0,0,0,'',0,0,0)" in text, name
        assert "CALL Park_V7 ( VAL MODE:=3,POSX:=0,POSY:=0)" in text, name
    # part size from the blank, e.g. a drawer bottom including its tongues in the grooves
    part = cabinet.layout.part("drawer_0_0_bottom")
    assert re.search(r"DY := ([\d.]+)", files["drawer_0_0_bottom_CUT_2.hop"]).group(1) == "{:g}".format(part.size[1] + 2 * 9)
    assert report.skipped == []


def test_hops_hinges(cabinet):
    door = export.hops(cabinet)[0]["door_0_2.hop"]
    # pots milled with the D20 router on a circle, 13 deep, as in the earlier programs
    assert "WZF (403," in door
    assert door.count("CALL _Kreisbahn_V5") == 4 and "RADIUS:=17.5" in door and "TIEFE:=13," in door
    # the screw holes with the D3 drill, the mounting plates' D5 holes in the side with the short D5: always the spindle
    assert "WZB (602," in door and door.count("Bohrung (") == 8 and ",3,-11,10," in door
    side = export.hops(cabinet)[0]["side_left.hop"]
    assert "WZB (609," in side and side.count(",37,5,-12,10,") == 8


def test_hops_clamex(cabinet):
    files, _ = export.hops(cabinet)
    # the edge halves with the disc (108) and access holes drilled with 135: three per end of the top
    top = files["top.hop"]
    assert top.count("CALL Clamex1_Stumpf (") == 2
    assert "CLAMEX_ID_H:=108" in top and "CLAMEX_B_ID:=135" in top and "ART:=0" in top
    assert top.count("B_POS:=5.5,B_DM:=6,") == 2
    left = next(line for line in top.splitlines() if "C1:=-1" in line)
    assert "EY1:=60" in left and "EY3:=60" in left and "C2:=-1" in left and "C4:=0" in left
    # the face halves with the finger mill, along the joint
    side = files["side_left.hop"]
    assert side.count("CALL Clamex_MS_FI") == 6 and "CL_WKZ:=136" in side and "DW:=90," in side
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
    assert tools.router_for(11.6, 9).number == 404
    assert tools.router_for(30, 9).number == 403
    assert tools.router_for(4, 9) is None
    # drillings: the shortest drill of that diameter that's long enough
    assert tools.drill_for(5, 12).number == 609
    assert tools.drill_for(8, 20).number == 603
    assert tools.drill_for(5, 40) is None
    assert tools.drill_for(7, 10) is None
    # shelf holes: the drill head if it has a bit for them, else a spindle drill
    assert tools.head_drill_for(5, 12).number == 501
    assert tools.head_drill_for(8, 12).number == 603
    assert HopsTools(drill_head_bits=()).head_drill_for(5, 12).number == 609


def test_hops_tools_from_tool_table(tmp_path):
    table = """[ToolsCount]
Tools=5
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
[ToolData4]
Name=CLAMEX_Drill
ToolNo=18
GroupName=Special
[ToolData4CuttingEdge0]
ID=137
Radius=3.5
UseableLength=35
"""
    path = tmp_path / "tools.too"
    path.write_bytes(table.encode("utf-16"))
    tools = HopsTools.from_too(str(path), clamex_disc=109)
    assert tools.routers == (Router(403, 20.0, 95.0, "Turbex D20"),)
    assert tools.drills == ()  # not in the tool changer
    assert tools.drill_head_bits == (Drill(501, 35.0, 50.0, "35 mm"),)
    assert tools.saw == 202 and tools.clamex_disc == 109
    assert tools.clamex_drill == 137 and tools.clamex_hole_diameter == 7


def test_hops_rabbets_are_single_lines(cabinet):
    # as in the earlier programs: one line along the rabbet's inner edge, radius correction 1, over the whole part
    side = export.hops(cabinet)[0]["side_left.hop"]
    assert "; Rabbet 8 wide, 9 deep" in side
    rabbet = side[side.index("; Rabbet") :].splitlines()
    assert rabbet[1] == "WZF (403,_VE,_V,_VA,_SD,_ANF,'1')"
    assert rabbet[2] == "SP (0,492,9,1,1,_ANF*2,0,0,0,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0.5mm)"  # Z: the floor above the underside
    assert rabbet[3:5] == ["G01 (DX,0,0,0,10,2)", "EP (1,_ANF*2,0)"]
    assert "TASCHENBREITE:=1252" not in side


def test_hops_identical_parts_share_a_program():
    cabinet = Cabinet(CabinetConfig.shelves_and_drawers(3, 3))
    files, report = export.hops(cabinet)
    # four equal shelves: one program, named after the first with how many to cut
    assert "shelf_0_2_CUT_4.hop" in files and not any(n.startswith("shelf_0_3") for n in files)
    assert report.copies["shelf_0_2_CUT_4"] == ["shelf_0_2", "shelf_0_3", "shelf_0_4", "shelf_0_5"]
    text = files["shelf_0_2_CUT_4.hop"]
    assert ";NCNAME=shelf_0_2_CUT_4\n" in text and ";BILD=SHELF_0_2_CUT_4.WMF\n" in text
    assert "shelf_0_2_CUT_4: shelf_0_2, shelf_0_3, shelf_0_4, shelf_0_5" in report.summary()
    # parts that differ keep their own programs, without a count
    assert "side_left.hop" in files and "side_right.hop" in files
    assert sum(len(parts) for parts in report.copies.values()) + len(files) - len(report.copies) == len(cabinet.plates)


def test_hops_stopped_dado_tenons_are_milled_from_the_end():
    cabinet = Cabinet(CabinetConfig.grid(2, 2, door_style="none", interior_joint="stopped_dado"))
    files, report = export.hops(cabinet)
    assert report.skipped == []
    shelf = files["shelf_0_0_CUT_2.hop"]
    divider = files["divider_0.hop"]
    # the dados are milled with the D13.4 router, so their closed ends have R6.7 corners
    assert "WZF (402," in divider and "TASCHENLAENGE:=18,TASCHENBREITE:=508," in divider
    # the shelf's notches: no pockets, a contour in each end's plane with arcs of the same radius, in two passes
    assert "_RechteckTasche_V5" not in shelf
    assert shelf.count("; Stopped dado tenon: 20 x 6 notch milled from the end, corners R6.7") == 2
    # (the shelf lies with its long edge, its depth, along X: its tenons are in its front and rear edges)
    assert shelf.count("Ebene1()") == shelf.count("Ebene3()") == 1 and shelf.count("Ebene0()") == 2
    right = shelf[shelf.index("Ebene3()") :].splitlines()
    assert right[1:6] == [
        "SP (20+6.7,0,-6,1,2,_ANF*2,10,0,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0.5mm)",  # 6 deep, allowance 10
        "G02R (20,6.7,0,6.7,0,1,2)",
        "G01 (20,DZ-6.7,0,0,1,2)",
        "G02R (20+6.7,DZ,0,6.7,0,1,2)",
        "EP (2,_ANF*2,0)",
    ]
    assert right[6] == "SP (20+6.7,0,-6,1,2,_ANF*2,0,0,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0.5mm)"  # then without
    assert right[11] == "Ebene0()"
    # at the other end the notch opens at the end of the plane's X: mirrored
    left = shelf[shelf.index("Ebene1()") :].splitlines()
    assert left[1].startswith("SP (DX-20-6.7,0,-6,2,") and left[2] == "G03R (DX-20,6.7,0,6.7,0,1,2)"
    # the divider lies with its height along X: its tenons are in its ends
    assert "Ebene2()" in divider and "Ebene4()" in divider


def test_hops_drawer_bottom_grooves_are_lines_without_lead_out(cabinet):
    files, _ = export.hops(cabinet)
    # through the drawer side: the D6 router along both walls of the 8 mm groove, out to both edges
    side = files["drawer_0_0_side_l.hop"]
    groove = side[side.index("; Groove 8 wide, 9 deep") :].splitlines()
    assert groove[1:8] == [
        "WZF (410,_VE,_V,_VA,_SD,_ANF,'1')",
        "SP (-6,10,9,1,1,_ANF*2,0,0,0,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0.5mm)",  # linear approach, tool left, inside
        "G01 (DX+6,10,0,0,1,2)",  # past the part's edges by a tool diameter
        "EP (0,_ANF*2,0)",  # no lead-out
        "SP (DX+6,18,9,1,1,_ANF*2,0,0,0,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0.5mm)",
        "G01 (-6,18,0,0,1,2)",
        "EP (0,_ANF*2,0)",
    ]
    # in the drawer front a rectangular pocket instead, as long as the groove
    front = files["drawer_0_0_front.hop"]
    assert "SP (" not in front
    assert "TASCHENLAENGE:=720,TASCHENBREITE:=8," in front and "WZF (410," in front


def test_hops_pockets_have_the_routers_corner_radius(cabinet):
    side = export.hops(cabinet)[0]["side_left.hop"]
    # the mortises, with the D13.4 router
    assert "WZF (402," in side and side.count("RADIUS:=6.7,") == 4 and "RADIUS:=0," not in side
    # and no lead-out
    assert side.count("AB:=0,") == 4 and "AB:=2," not in side


def test_hops_drawer_parts_lie_with_their_bottom_edge_along_x(cabinet):
    back = export.hops(cabinet)[0]["drawer_0_0_back.hop"]
    blank = cabinet.plates["drawer_0_0_back"].blank
    # X along the back's width (its blank's y, into the sides' grooves), its bottom groove along X near Y = 0
    assert "DX := {:g};".format(blank.ysize) in back and "DY := {:g};".format(blank.xsize) in back
    assert blank.ysize > blank.xsize
    assert "SP (-6,10,9,1,1," in back and "G01 (DX+6,10,0,0,1,2)" in back
    # the sides too, the right one turned half round (it's machined from its other face)
    files = export.hops(cabinet)[0]
    for side in ("drawer_0_0_side_l.hop", "drawer_0_0_side_r.hop"):
        assert "SP (-6,10,9,1,1," in files[side] and "G01 (DX+6,10,0,0,1,2)" in files[side], side
    assert "HorzB (472,30,9,8,-20,0,0,180,1,0,0,0)" in files["drawer_0_0_side_r.hop"]
    # and the front, whose groove is higher as the front reaches below the box
    front = files["drawer_0_0_front.hop"]
    assert "DX := 797;" in front and "DY := 297;" in front
    assert "X_MITTE:=398.5,Y_MITTE:=30.5,TASCHENLAENGE:=720,TASCHENBREITE:=8," in front


def _dxf_pairs(text):
    lines = text.split("\n")
    return [(int(lines[i]), lines[i + 1]) for i in range(0, len(lines) - 1, 2)]


def test_dxf(cabinet, tmp_path):
    from cabi_neat.dxf import layer_name

    path = tmp_path / "outlines.dxf"
    text, report = export.dxf(cabinet, str(path))
    assert report.errors == []
    assert b"\r\n" in path.read_bytes()
    pairs = _dxf_pairs(text)
    assert (1, "AC1009") in pairs
    # one block and one insert per part, on the layer of its thickness
    assert pairs.count((0, "BLOCK")) == len(cabinet.plates)
    inserts = [i for i, p in enumerate(pairs) if p == (0, "INSERT")]
    assert len(inserts) == len(cabinet.plates)
    layers = {pairs[i + 1][1] for i in inserts}
    assert layers == {layer_name(p.thickness) for p in cabinet.plates.values()}
    assert text.count("\nTEXT\n") == len(cabinet.plates)
    for name in cabinet.plates:
        assert "\n{}\n".format(name.upper()) in text and "\n{}\n".format(name) in text


def test_dxf_outlines(cabinet):
    from cabi_neat.dxf import plate_outline

    # a shelf with two tenons on each end: its outline has them
    shelf = cabinet.plates[[n for n in cabinet.plates if n.startswith("shelf")][0]]
    outline, pieces = plate_outline(shelf)
    assert pieces == 1
    assert len(outline) == 4 + 2 * 2 * 4
    xs, ys = zip(*outline)
    box = shelf.modelgeometry.aabb
    assert max(xs) == pytest.approx(box.xmax - box.xmin, abs=0.01)
    assert max(ys) == pytest.approx(box.ymax - box.ymin, abs=0.01)
    # a side with pockets and grooves that don't go through is a plain rectangle
    outline, _ = plate_outline(cabinet.plates["side_left"])
    assert sorted(outline) == pytest.approx(sorted([(0, 0), (500, 0), (500, 1200), (0, 1200)]), abs=0.01)


def test_dxf_arrangement():
    from cabi_neat.dxf import DxfPart
    from cabi_neat.dxf import arrange

    parts = [
        DxfPart("p{}".format(i), t, [(0, 0), (w, 0), (w, h), (0, h)])
        for i, (t, w, h) in enumerate([(18, 800, 500), (18, 1200, 500), (18, 2500, 400), (8, 700, 700), (18, 300, 200)])
    ]
    placed = arrange(parts, row_width=3000, gap=50)
    boxes = {p.name: (x, y, x + p.width, y + p.height) for p, (x, y) in placed}
    names = list(boxes)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            A, B = boxes[a], boxes[b]
            assert A[2] + 50 <= B[0] + 1e-9 or B[2] + 50 <= A[0] + 1e-9 or A[3] + 50 <= B[1] + 1e-9 or B[3] + 50 <= A[1] + 1e-9, (a, b)
    # thinnest first, at the top; each thickness in its own band below
    assert boxes["p3"][1] > max(boxes[n][3] for n in ("p0", "p1", "p2", "p4"))
    assert all(boxes[n][2] <= 3000 for n in names)


def test_dxf_reads_back(cabinet, tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    path = tmp_path / "outlines.dxf"
    export.dxf(cabinet, str(path))
    doc = ezdxf.readfile(str(path))
    assert doc.dxfversion == "AC1009"
    assert not doc.audit().has_errors
    inserts = doc.modelspace().query("INSERT")
    assert len(inserts) == len(cabinet.plates)
    block = doc.blocks.get("SIDE_LEFT")
    assert [e.dxftype() for e in block] == ["POLYLINE", "TEXT"]
    assert block.query("POLYLINE")[0].is_closed


def test_hops_shelf_holes_use_the_drill_head():
    from cabi_neat.config import Cell

    cabinet = Cabinet(CabinetConfig(interior=Cell(door_style="single", shelf_holes=True)))
    side = export.hops(cabinet)[0]["side_left.hop"]
    drilled = len([h for h in cabinet.layout.holes if h.part == "side_left"]) - len(cabinet.skipped_holes)
    assert "WZB (501,_VE,_V,_VA,_SD,_ANF,'1')" in side
    assert side.count("(drill head)") == drilled
    assert side.count(",5,-8,10,") == drilled
    # the hinge plates' holes still use the spindle drill
    assert "WZB (609," in side and side.count(",5,-12,10,") == 8
