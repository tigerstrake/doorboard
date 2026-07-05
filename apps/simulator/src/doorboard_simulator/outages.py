from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OutageState:
    nuc_reachable: bool = True
    nas_reachable: bool = True
    hailo_ok: bool = True
    storage_full: bool = False

    def set_nuc(self, reachable: bool) -> None:
        self.nuc_reachable = reachable

    def set_nas(self, reachable: bool) -> None:
        self.nas_reachable = reachable

    def set_hailo(self, ok: bool) -> None:
        self.hailo_ok = ok

    def set_storage_full(self, full: bool) -> None:
        self.storage_full = full
