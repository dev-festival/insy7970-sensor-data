from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoragePaths:
    data_dir: Path

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def raw_waites_dir(self) -> Path:
        return self.raw_dir / "waites"

    @property
    def raw_maximo_dir(self) -> Path:
        return self.raw_dir / "maximo"

    @property
    def processed_waites_dir(self) -> Path:
        return self.processed_dir / "waites"

    @property
    def snapshots_dir(self) -> Path:
        return self.processed_dir / "snapshots"

    @property
    def trends_dir(self) -> Path:
        return self.processed_dir / "trends"

    @property
    def clusters_dir(self) -> Path:
        return self.processed_dir / "clusters"

    @property
    def drift_dir(self) -> Path:
        return self.processed_dir / "drift"

    def ensure_base_dirs(self) -> list[Path]:
        dirs = [
            self.raw_waites_dir,
            self.raw_maximo_dir,
            self.processed_waites_dir,
            self.snapshots_dir,
            self.trends_dir,
            self.clusters_dir,
            self.drift_dir,
        ]
        for path in dirs:
            path.mkdir(parents=True, exist_ok=True)
        return dirs


def get_storage_paths(data_dir: str | Path) -> StoragePaths:
    return StoragePaths(data_dir=Path(data_dir))
