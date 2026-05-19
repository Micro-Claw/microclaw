from __future__ import annotations
from pycromanager import Core, Studio


class MicroscopeController:
    """Thin wrapper around pycro-manager Core and Studio.
    Holds the ZMQ connection; all tool functions go through here."""

    def __init__(self, port: int = 4827):
        self._core = Core(port=port)
        self._studio = Studio(port=port)

    @property
    def core(self) -> Core:
        return self._core

    @property
    def studio(self) -> Studio:
        return self._studio

    def is_connected(self) -> bool:
        try:
            self._core.get_version_info()
            return True
        except Exception:
            return False

    # --- Position list management (MM native API) ---

    def _pos_list(self):
        """Return MM's live PositionList Java object via pycro-manager."""
        return self._studio.positions().get_position_list()

    def add_position(self, label: str, x: float, y: float, z: float | None = None) -> None:
        """Add a position to MM's native position list."""
        from pycromanager import MultiStagePosition, StagePosition
        msp = MultiStagePosition()
        msp.set_label(label)
        xy_stage = self._core.get_xy_stage_device()
        sp_xy = StagePosition()
        sp_xy.stage_device_label = xy_stage
        sp_xy.num_axes = 2
        sp_xy.x = x
        sp_xy.y = y
        msp.add(sp_xy)
        if z is not None:
            z_stage = self._core.get_focus_device()
            sp_z = StagePosition()
            sp_z.stage_device_label = z_stage
            sp_z.num_axes = 1
            sp_z.x = z
            msp.add(sp_z)
        self._pos_list().add_position(msp)

    def get_positions(self) -> list[dict]:
        """Return all positions from MM's native list."""
        pl = self._pos_list()
        out = []
        for i in range(pl.get_number_of_positions()):
            msp = pl.get_position(i)
            entry: dict = {"name": str(msp.get_label())}
            for j in range(msp.size()):
                sp = msp.get(j)
                if sp.num_axes == 2:
                    entry["x_um"] = round(sp.x, 3)
                    entry["y_um"] = round(sp.y, 3)
                elif sp.num_axes == 1:
                    entry["z_um"] = round(sp.x, 3)
            out.append(entry)
        return out

    def go_to_position(self, label: str) -> None:
        """Move stage to a named position from MM's native list."""
        pl = self._pos_list()
        for i in range(pl.get_number_of_positions()):
            msp = pl.get_position(i)
            if str(msp.get_label()) == label:
                pl.go_to_position(i, self._core)
                return
        raise KeyError(f"Position '{label}' not found in MM position list.")

    def remove_position(self, label: str) -> None:
        """Remove a named position from MM's native list."""
        pl = self._pos_list()
        for i in range(pl.get_number_of_positions()):
            if str(pl.get_position(i).get_label()) == label:
                pl.remove_position(i)
                return
        raise KeyError(f"Position '{label}' not found.")

    def clear_positions(self) -> None:
        """Clear all positions from MM's native list."""
        self._pos_list().clear_all_positions()

    def save_position_list(self, path: str) -> None:
        """Save MM position list to a .pos file."""
        self._pos_list().save(path)

    def load_position_list(self, path: str) -> None:
        """Load a .pos file into MM's native position list."""
        pl = self._pos_list()
        pl.load(path)
        self._studio.positions().set_position_list(pl)
