"""A 3 x 4 inset shelving unit with double doors, then shown in the configurator."""

from cabi_neat import Cabinet
from cabi_neat import CabinetConfig
from cabi_neat import Column
from cabi_neat import Zone
from cabi_neat import ZoneKind

config = CabinetConfig.grid(3, 4, width=1500, height=1600, door_mount="inset", door_style="double")
# make the middle column open (no doors) with a drawer at the bottom
config.columns[1] = Column(zones=[Zone(ZoneKind.DRAWER, 180)] + [Zone() for _ in range(3)], door_style="none")

cabinet = Cabinet(config)
print(cabinet.format_cut_list())

if __name__ == "__main__":
    from cabi_neat.configurator import Configurator

    Configurator(config).start()
