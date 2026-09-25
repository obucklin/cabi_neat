"""A single-column cabinet with two drawers below two shelves and an overlay door."""

from cabi_neat import Cabinet
from cabi_neat import CabinetConfig

config = CabinetConfig.shelves_and_drawers(shelves=2, drawers=2, width=600, height=1400, depth=450, hinge_side="right")
cabinet = Cabinet(config)

print(cabinet.format_cut_list())
print("{} plates, {} joints, {} errors".format(len(cabinet.layout.parts), len(list(cabinet.model.joints)), len(cabinet.errors)))
