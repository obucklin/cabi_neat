"""Fabrication output: BTLx (through compas_timber), HOPS (see :mod:`cabi_neat.hops`) and part outlines as DXF (see
:mod:`cabi_neat.dxf`).

Both work from a built :class:`cabi_neat.Cabinet` and return a report of what they left out. Features that aren't
BTLx processings (the Clamex P-14 grooves) can't go into BTLx; the HOPS programs make them with the Clamex macros.
"""

import io
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import field
from typing import Dict
from typing import List

from compas_timber.fabrication import BTLxProcessing
from compas_timber.fabrication import BTLxWriter

# ==========================================================================
# BTLx
# ==========================================================================


@dataclass
class ExportReport:
    """What an export produced and what it had to leave out."""

    skipped: List[str] = field(default_factory=list)  # human-readable, one entry per left-out feature
    errors: List[str] = field(default_factory=list)
    copies: Dict[str, List[str]] = field(default_factory=dict)  # file written once -> the parts it's cut for

    def summary(self):
        lines = []
        if self.copies:
            lines.append("{} program(s) for several identical parts:".format(len(self.copies)))
            lines.extend("  {}: {}".format(name, ", ".join(parts)) for name, parts in self.copies.items())
        if self.skipped:
            lines.append("{} feature(s) not exported:".format(len(self.skipped)))
            lines.extend("  " + s for s in self.skipped)
        if self.errors:
            lines.append("{} error(s):".format(len(self.errors)))
            lines.extend("  " + e for e in self.errors)
        return "\n".join(lines) or "Everything was exported."


def _describe(feature):
    names = {"P14Edge": "Clamex P-14 groove (edge)", "P14Face": "Clamex P-14 groove (face)"}
    return names.get(type(feature).__name__, type(feature).__name__)


@contextmanager
def only_btlx_features(model):
    """Temporarily hide the plates' features that aren't BTLx processings, e.g. Clamex P-14 grooves.

    Yields ``{element: [hidden features]}``. The features are restored afterwards, in their original order.
    """
    originals = {}
    hidden = {}
    for element in model.elements():
        features = getattr(element, "_features", None)
        if not features:
            continue
        other = [f for f in features if not isinstance(f, BTLxProcessing)]
        if other:
            originals[element] = features
            hidden[element] = other
            # set the list directly: the public API would reset the element's cached geometry, which doesn't change
            element._features = [f for f in features if isinstance(f, BTLxProcessing)]
    try:
        yield hidden
    finally:
        for element, features in originals.items():
            element._features = features


def btlx(cabinet, filepath=None, project_name="cabi_neat"):
    """The cabinet's BTLx, as an XML string, written to ``filepath`` if given.

    Returns
    -------
    tuple(str, :class:`ExportReport`)
    """
    writer = BTLxWriter(project_name=project_name)
    report = ExportReport()
    with only_btlx_features(cabinet.model) as hidden:
        xml = writer.model_to_xml(cabinet.model)
    for element, features in hidden.items():
        report.skipped.extend("{}: {} (not a BTLx processing)".format(element.name, _describe(f)) for f in features)
    report.errors.extend(str(e) for e in getattr(writer, "errors", []) or [])
    if filepath:
        with open(filepath, "w") as f:
            f.write(xml)
    return xml, report


# ==========================================================================
# HOPS
# ==========================================================================


def _access_directions(cabinet):
    """Where each plate's Clamex access holes should face (world vectors).

    Horizontal plates: down (the underside of shelves and of the top), except the carcass bottom: up, into the cabinet.
    Upright plates: towards the middle of their assembly (the drawer box they belong to, else the carcass).
    """
    centers = {}
    for name, plate in cabinet.plates.items():
        centers.setdefault(_assembly(name), []).append(list(plate.blank.frame.point))
    centers = {k: [sum(c) / len(points) for c in zip(*points)] for k, points in centers.items()}
    directions = {}
    for part in cabinet.layout.parts:
        frame = cabinet.plates[part.name].blank.frame
        normal = frame.zaxis
        if abs(normal[2]) > 0.5:
            directions[part.name] = (0, 0, 1) if part.category == "bottom" else (0, 0, -1)
        else:
            center = centers[_assembly(part.name)]
            directions[part.name] = tuple(c - p for c, p in zip(center, frame.point))
    return directions


def _assembly(name):
    return "_".join(name.split("_")[:3]) if name.startswith("drawer_") else "carcass"


def hops(cabinet, directory=None, tools=None):
    """One HOPS program per plate of the cabinet, written to ``directory`` as ``<part name>.hop`` if given.

    Identical parts (e.g. shelves, drawer bottoms) share one program, named after the first of them with ``_CUT_<n>``
    appended: ``shelf_0_1_CUT_3.hop`` is cut three times. ``report.copies`` lists which parts each one is for.

    See :mod:`cabi_neat.hops` for what they contain and :class:`cabi_neat.hops.HopsTools` for the tools.

    Returns
    -------
    tuple(dict(str, str), :class:`ExportReport`)
        ``{file name: HOPS text}`` and what was left out.
    """
    import os

    from .hops import HopsTools
    from .hops import PlateProgram
    from .hops import TenonNotch
    from .layout import DRAWER_BOX

    tools = tools or HopsTools()
    report = ExportReport()
    programs = {}  # the machining (the text without the part's name) -> [(part name, text)]
    directions = _access_directions(cabinet)
    # the notches beside the tenons of stopped dados and mortises and tenons, to be milled from the end:
    # {plate: {id(feature): notch}}
    notches = {}
    grooves = {}  # the pockets of grooves (drawer bottoms), milled as lines: {plate: {id(feature)}}
    # except in visible drawer fronts: a rectangular pocket there
    pocket_grooves = {cabinet.plates[part.name] for part in cabinet.layout.parts if part.category == "drawer_front"}
    for joint in cabinet.model.joints:
        if type(joint).__name__ == "PlateGrooveJoint" and joint.cross_plate not in pocket_grooves:
            grooves.setdefault(joint.cross_plate, set()).update(id(f) for f in joint.features if f in joint.cross_plate.features)
        kind = {"PlateStoppedDadoJoint": "Stopped dado tenon", "PlateMortiseTenon": "Mortise tenon"}.get(type(joint).__name__)
        if kind is None:
            continue
        notch = TenonNotch(joint.pocket_depth, joint.main_plate.thickness, joint.pocket_depth, kind)
        for feature in joint.features:
            if feature in joint.main_plate.features:
                notches.setdefault(joint.main_plate, {})[id(feature)] = notch
    # drawer parts lie with their bottom edge (and the bottom's groove) along X at Y = 0
    drawer_parts = DRAWER_BOX + ("drawer_front",)
    for part in cabinet.layout.parts:
        plate = cabinet.plates[part.name]
        down = (0, 0, -1) if part.category in drawer_parts else None
        program = PlateProgram(plate, tools, directions.get(part.name), part.category, tenon_notches=notches.get(plate), grooves=grooves.get(plate), down=down)
        try:
            text = program.text()
        except Exception as e:  # keep going: one part's problem shouldn't stop the others
            report.errors.append("{}: {}".format(part.name, e))
            continue
        report.skipped.extend(program.skipped)
        programs.setdefault(_without_name(text), []).append((part.name, text))
    files: Dict[str, str] = {}
    for parts in programs.values():
        name, text = parts[0]
        if len(parts) > 1:
            name = "{}_CUT_{}".format(name, len(parts))
            text = _renamed(text, name)
            report.copies[name] = [p for p, _ in parts]
        files["{}.hop".format(name)] = text
    if directory:
        os.makedirs(directory, exist_ok=True)
        for name, text in files.items():
            with open(os.path.join(directory, name), "wb") as f:
                f.write(hops_bytes(text))
    return files, report


_NAME_LINES = (";BILD=", ";NCNAME=")


def _without_name(text):
    return "\n".join(line for line in text.split("\n") if not line.startswith(_NAME_LINES))


def _renamed(text, name):
    from .hops import _ascii

    lines = []
    for line in text.split("\n"):
        if line.startswith(";BILD="):
            line = ";BILD={}.WMF".format(_ascii(name).upper())
        elif line.startswith(";NCNAME="):
            line = ";NCNAME={}".format(_ascii(name))
        lines.append(line)
    return "\n".join(lines)


def hops_bytes(text):
    """A program as the machine's files are: Windows line endings, Latin-1."""
    return text.replace("\n", "\r\n").encode("latin-1", "replace")


def hops_zip(cabinet, tools=None):
    """All HOPS jobs of the cabinet in a zip, plus a report.txt of what was left out. Returns ``(bytes, report)``."""
    files, report = hops(cabinet, tools=tools)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            archive.writestr(name, hops_bytes(text))
        archive.writestr("report.txt", report.summary())
    return buffer.getvalue(), report


# ==========================================================================
# DXF
# ==========================================================================


def dxf(cabinet, filepath=None):
    """The outlines of all the cabinet's parts in one DXF R12 file, for nesting, written to ``filepath`` if given.

    See :mod:`cabi_neat.dxf` for what it contains.

    Returns
    -------
    tuple(str, :class:`ExportReport`)
    """
    from .dxf import DxfPart
    from .dxf import dxf_bytes
    from .dxf import plate_outline
    from .dxf import to_dxf

    report = ExportReport()
    parts = []
    for part in cabinet.layout.parts:
        plate = cabinet.plates[part.name]
        try:
            outline, pieces = plate_outline(plate)
        except Exception as e:  # keep going: one part's problem shouldn't stop the others
            report.errors.append("{}: {}".format(part.name, e))
            continue
        if pieces > 1:
            report.errors.append("{}: the part falls apart into {} pieces, only the largest is drawn".format(part.name, pieces))
        parts.append(DxfPart(part.name, plate.thickness, outline))
    text = to_dxf(parts)
    if filepath:
        with open(filepath, "wb") as f:
            f.write(dxf_bytes(text))
    return text, report


def _short_summary(report):
    """A one-line summary of a report, e.g. for a response header."""
    from collections import Counter

    kinds = Counter(s.split(": ", 1)[-1].split(" (")[0].split(" -")[0] for s in report.skipped)
    parts = ["{} x {}".format(n, kind) for kind, n in kinds.most_common()]
    return ("Not exported: " + "; ".join(parts)) if parts else "Everything was exported."


def main():
    """Export a cabinet config: ``python -m cabi_neat.export cabinet.json --btlx out.btlx --hops out/ --dxf outlines.dxf``."""
    import argparse

    from .builder import Cabinet
    from .config import CabinetConfig

    parser = argparse.ArgumentParser(description="Export a cabinet to BTLx, HOPS and/or DXF.")
    parser.add_argument("config", help="Cabinet config JSON file.")
    parser.add_argument("--btlx", help="BTLx file to write.")
    parser.add_argument("--hops", help="Directory to write one .hop file per part into.")
    parser.add_argument("--dxf", help="DXF file to write the part outlines to, for nesting.")
    args = parser.parse_args()
    if not (args.btlx or args.hops or args.dxf):
        parser.error("give --btlx, --hops and/or --dxf")
    cabinet = Cabinet(CabinetConfig.from_json(args.config))
    for error in cabinet.errors:
        print("Joinery: {}".format(error))
    if args.btlx:
        _, report = btlx(cabinet, args.btlx)
        print("BTLx written to {}. {}".format(args.btlx, report.summary()))
    if args.hops:
        files, report = hops(cabinet, args.hops)
        print("{} HOPS files written to {}. {}".format(len(files), args.hops, report.summary()))
    if args.dxf:
        _, report = dxf(cabinet, args.dxf)
        print("DXF written to {}. {}".format(args.dxf, report.summary()))


if __name__ == "__main__":
    main()
