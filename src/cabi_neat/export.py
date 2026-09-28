"""Fabrication output: BTLx (through compas_timber) and HOPS (see :mod:`cabi_neat.hops`).

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

    def summary(self):
        lines = []
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

    See :mod:`cabi_neat.hops` for what they contain and :class:`cabi_neat.hops.HopsTools` for the tools.

    Returns
    -------
    tuple(dict(str, str), :class:`ExportReport`)
        ``{file name: HOPS text}`` and what was left out.
    """
    import os

    from .hops import HopsTools
    from .hops import PlateProgram

    tools = tools or HopsTools()
    report = ExportReport()
    files: Dict[str, str] = {}
    directions = _access_directions(cabinet)
    for part in cabinet.layout.parts:
        program = PlateProgram(cabinet.plates[part.name], tools, directions.get(part.name), comment=part.category)
        try:
            text = program.text()
        except Exception as e:  # keep going: one part's problem shouldn't stop the others
            report.errors.append("{}: {}".format(part.name, e))
            continue
        report.skipped.extend(program.skipped)
        files["{}.hop".format(part.name)] = text
    if directory:
        os.makedirs(directory, exist_ok=True)
        for name, text in files.items():
            with open(os.path.join(directory, name), "wb") as f:
                f.write(hops_bytes(text))
    return files, report


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


def _short_summary(report):
    """A one-line summary of a report, e.g. for a response header."""
    from collections import Counter

    kinds = Counter(s.split(": ", 1)[-1].split(" (")[0].split(" -")[0] for s in report.skipped)
    parts = ["{} x {}".format(n, kind) for kind, n in kinds.most_common()]
    return ("Not exported: " + "; ".join(parts)) if parts else "Everything was exported."


def main():
    """Export a cabinet config to BTLx and/or HOPS: ``python -m cabi_neat.export cabinet.json --btlx out.btlx --hops out/``."""
    import argparse

    from .builder import Cabinet
    from .config import CabinetConfig

    parser = argparse.ArgumentParser(description="Export a cabinet to BTLx and/or HOPS.")
    parser.add_argument("config", help="Cabinet config JSON file.")
    parser.add_argument("--btlx", help="BTLx file to write.")
    parser.add_argument("--hops", help="Directory to write one .hop file per part into.")
    args = parser.parse_args()
    if not (args.btlx or args.hops):
        parser.error("give --btlx and/or --hops")
    cabinet = Cabinet(CabinetConfig.from_json(args.config))
    for error in cabinet.errors:
        print("Joinery: {}".format(error))
    if args.btlx:
        _, report = btlx(cabinet, args.btlx)
        print("BTLx written to {}. {}".format(args.btlx, report.summary()))
    if args.hops:
        files, report = hops(cabinet, args.hops)
        print("{} HOPS files written to {}. {}".format(len(files), args.hops, report.summary()))


if __name__ == "__main__":
    main()
