from __future__ import annotations
import json
from pathlib import Path

from pycromanager import Core, Studio


class MicroscopeController:
    """Thin wrapper around pycro-manager Core and Studio.
    Holds the ZMQ connection; all tool functions go through here."""

    def __init__(self, port: int = 4827):
        self._core = Core(port=port)
        self._studio = Studio(port=port)
        # Python-native position store. Use import_from_mm_position_list() to
        # pull in positions the user has marked in MM's GUI.
        self._positions: list[dict] = []

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

    # --- Position list management ---

    def _read_mm_position_list(self) -> list[dict]:
        """Read positions from MM's GUI position list (read-only Java access)."""
        pl = self._studio.positions().get_position_list()
        out = []
        for i in range(pl.get_number_of_positions()):
            msp = pl.get_position(i)
            entry: dict = {"name": str(msp.get_label())}
            for j in range(msp.size()):
                sp = msp.get(j)
                if sp.num_axes == 2:
                    entry["x_um"] = round(float(sp.x), 3)
                    entry["y_um"] = round(float(sp.y), 3)
                elif sp.num_axes == 1:
                    entry["z_um"] = round(float(sp.x), 3)
            out.append(entry)
        return out

    def import_from_mm_position_list(self) -> list[str]:
        """Copy positions from MM's GUI position list into the internal store.

        Existing entries with the same name are replaced. Returns the list of
        imported position names.
        """
        imported = []
        for pos in self._read_mm_position_list():
            self._positions = [p for p in self._positions if p["name"] != pos["name"]]
            self._positions.append(pos)
            imported.append(pos["name"])
        return imported

    def add_position(self, label: str, x: float, y: float, z: float | None = None) -> None:
        """Add or replace a named position in the internal store."""
        entry: dict = {"name": label, "x_um": round(x, 3), "y_um": round(y, 3)}
        if z is not None:
            entry["z_um"] = round(z, 3)
        self._positions = [p for p in self._positions if p["name"] != label]
        self._positions.append(entry)

    def get_positions(self) -> list[dict]:
        """Return all stored positions."""
        return list(self._positions)

    def go_to_position(self, label: str) -> None:
        """Move stage to a named position."""
        for pos in self._positions:
            if pos["name"] == label:
                self._core.set_xy_position(pos["x_um"], pos["y_um"])
                self._core.wait_for_device(self._core.get_xy_stage_device())
                if "z_um" in pos:
                    self._core.set_position(pos["z_um"])
                    self._core.wait_for_device(self._core.get_focus_device())
                return
        raise KeyError(f"Position '{label}' not found.")

    def remove_position(self, label: str) -> None:
        """Remove a named position."""
        before = len(self._positions)
        self._positions = [p for p in self._positions if p["name"] != label]
        if len(self._positions) == before:
            raise KeyError(f"Position '{label}' not found.")

    def clear_positions(self) -> None:
        """Clear all stored positions."""
        self._positions = []

    def save_position_list(self, path: str) -> None:
        """Persist the position list to a JSON file."""
        Path(path).write_text(json.dumps(self._positions, indent=2))

    def load_position_list(self, path: str) -> None:
        """Load positions from a JSON file written by save_position_list."""
        self._positions = json.loads(Path(path).read_text())
