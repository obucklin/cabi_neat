"""2D part outlines as a DXF, for planning nesting.

One file for the whole cabinet, written as DXF R12 (AC1009), which every CAD and nesting program reads:

- Each part is a block named after it, holding the part's outline (a closed ``POLYLINE``) and its name (a ``TEXT``).
- Every part's block is inserted once, laid out in rows, one band of rows per material thickness.
- Everything of a part (the insert, outline and name) is on the layer of its thickness, e.g. ``18MM`` or ``8_5MM``.

The outline is the processed plate's silhouette, seen from the plate frame's +z side: it includes what joints add or
cut away through the whole thickness (tenons, stopped dado notches, a back extended into rabbets), and none of the
machining that doesn't go through (pockets, grooves, drillings). Coordinates are in millimetres.
"""

from shapely.geometry import MultiPolygon
from shapely.geometry import Polygon
from shapely.ops import unary_union

# snap the tessellation to this grid before the union, so neighbouring triangles share their edges exactly
_GRID = 1e-3


def plate_outline(plate, tolerance=0.01):
    """The outline of a processed plate, as ``[(x, y), ...]`` (closed, counter-clockwise, without repeating the first point).

    ``x`` and ``y`` run along the plate frame's x and y axes, shifted so the outline starts at 0.

    Returns
    -------
    tuple(list, int)
        The outline and the number of separate pieces the silhouette had (only the largest one is returned).
    """
    frame = plate.frame
    origin, xaxis, yaxis = list(frame.point), list(frame.xaxis), list(frame.yaxis)

    def local(point):
        d = [p - o for p, o in zip(point, origin)]
        return (round(sum(a * b for a, b in zip(d, xaxis)) / _GRID) * _GRID, round(sum(a * b for a, b in zip(d, yaxis)) / _GRID) * _GRID)

    polygons = []
    for mesh in plate.modelgeometry.to_meshes():
        for face in mesh.faces():
            polygon = Polygon([local(p) for p in mesh.face_coordinates(face)])
            if polygon.is_valid and polygon.area > _GRID:
                polygons.append(polygon)
    shape = unary_union(polygons)
    pieces = list(shape.geoms) if isinstance(shape, MultiPolygon) else [shape]
    largest = max(pieces, key=lambda p: p.area)
    outline = Polygon(largest.exterior).simplify(tolerance)
    if not outline.exterior.is_ccw:
        outline = Polygon(list(outline.exterior.coords)[::-1])
    xmin, ymin, _, _ = outline.bounds
    points = [(x - xmin, y - ymin) for x, y in outline.exterior.coords[:-1]]
    return points, len(pieces)


def layer_name(thickness):
    """``18MM``, ``8_5MM``: R12 names can't contain dots."""
    return "{}MM".format(_n(thickness).replace(".", "_").replace("-", "M"))


def _n(value):
    text = "{:.4f}".format(value).rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _name(text):
    """A valid R12 table/block name: upper case letters, digits, ``$``, ``-`` and ``_``."""
    return "".join(c if c.isalnum() and c.isascii() or c in "$-_" else "_" for c in text.upper())


class DxfPart:
    """A part to draw: its name, thickness and outline (see :func:`plate_outline`)."""

    def __init__(self, name, thickness, outline):
        self.name = name
        self.thickness = thickness
        self.outline = outline
        xs, ys = zip(*outline)
        self.width = max(xs)
        self.height = max(ys)


def _text_height(part):
    """Big enough to read, small enough to fit inside the part."""
    height = min(40.0, max(5.0, 0.15 * min(part.width, part.height)))
    # a character is roughly 0.7 of the text height wide in the STANDARD style
    return min(height, 0.8 * part.width / (0.7 * max(1, len(part.name))))


def _label_point(part):
    polygon = Polygon(part.outline)
    center = polygon.centroid
    if not polygon.contains(center):
        center = polygon.representative_point()
    return center.x, center.y


def arrange(parts, row_width=3000.0, gap=50.0):
    """Where to insert each part's block: rows left to right, wrapped at ``row_width``, one band per thickness (thinnest first,
    going down from the origin). Returns ``[(part, (x, y)), ...]``."""
    placed = []
    y = 0.0
    for thickness in sorted({p.thickness for p in parts}):
        group = sorted((p for p in parts if p.thickness == thickness), key=lambda p: (-p.height, p.name))
        width = max(row_width, max(p.width for p in group))
        x, row_height = 0.0, 0.0
        for part in group:
            if x > 0 and x + part.width > width:
                y -= row_height + gap
                x, row_height = 0.0, 0.0
            if row_height == 0.0:
                row_height = part.height
            placed.append((part, (x, y - part.height)))
            x += part.width + gap
        y -= row_height + 2 * gap
    return placed


def to_dxf(parts, row_width=3000.0, gap=50.0):
    """The DXF R12 text for ``parts`` (:class:`DxfPart`), see the module docstring."""
    placed = arrange(parts, row_width, gap)
    layers = sorted({p.thickness for p in parts})
    out = []

    def add(code, value):
        out.append("{:>3}".format(code))
        out.append(value if isinstance(value, str) else _n(value))

    xs = [x for part, (x, _) in placed] + [x + part.width for part, (x, _) in placed]
    ys = [y for part, (_, y) in placed] + [y + part.height for part, (_, y) in placed]
    add(0, "SECTION"), add(2, "HEADER")
    add(9, "$ACADVER"), add(1, "AC1009")
    add(9, "$EXTMIN"), add(10, min(xs, default=0)), add(20, min(ys, default=0)), add(30, 0)
    add(9, "$EXTMAX"), add(10, max(xs, default=0)), add(20, max(ys, default=0)), add(30, 0)
    add(0, "ENDSEC")

    add(0, "SECTION"), add(2, "TABLES")
    add(0, "TABLE"), add(2, "LTYPE"), add(70, "1")
    add(0, "LTYPE"), add(2, "CONTINUOUS"), add(70, "0"), add(3, "Solid line"), add(72, "65"), add(73, "0"), add(40, 0)
    add(0, "ENDTAB")
    add(0, "TABLE"), add(2, "LAYER"), add(70, str(len(layers) + 1))
    for i, name in enumerate(["0"] + [layer_name(t) for t in layers]):
        add(0, "LAYER"), add(2, name), add(70, "0"), add(62, str(7 if i == 0 else 1 + (i - 1) % 6)), add(6, "CONTINUOUS")
    add(0, "ENDTAB")
    add(0, "TABLE"), add(2, "STYLE"), add(70, "1")
    add(0, "STYLE"), add(2, "STANDARD"), add(70, "0"), add(40, 0), add(41, 1), add(50, 0), add(71, "0"), add(42, 2.5), add(3, "txt"), add(4, "")
    add(0, "ENDTAB")
    add(0, "ENDSEC")

    add(0, "SECTION"), add(2, "BLOCKS")
    for part in parts:
        layer, block = layer_name(part.thickness), _name(part.name)
        add(0, "BLOCK"), add(8, "0"), add(2, block), add(70, "0"), add(10, 0), add(20, 0), add(30, 0), add(3, block), add(1, "")
        add(0, "POLYLINE"), add(8, layer), add(66, "1"), add(10, 0), add(20, 0), add(30, 0), add(70, "1")
        for x, y in part.outline:
            add(0, "VERTEX"), add(8, layer), add(10, x), add(20, y), add(30, 0)
        add(0, "SEQEND"), add(8, layer)
        x, y = _label_point(part)
        add(0, "TEXT"), add(8, layer), add(10, x), add(20, y), add(30, 0), add(40, _text_height(part)), add(1, part.name)
        add(72, "4"), add(11, x), add(21, y), add(31, 0)
        add(0, "ENDBLK"), add(8, "0")
    add(0, "ENDSEC")

    add(0, "SECTION"), add(2, "ENTITIES")
    for part, (x, y) in placed:
        add(0, "INSERT"), add(8, layer_name(part.thickness)), add(2, _name(part.name)), add(10, x), add(20, y), add(30, 0)
    add(0, "ENDSEC")
    add(0, "EOF")
    return "\n".join(out) + "\n"


def dxf_bytes(text):
    """A DXF as old readers expect it: Windows line endings, ASCII."""
    return text.replace("\n", "\r\n").encode("ascii", "replace")
