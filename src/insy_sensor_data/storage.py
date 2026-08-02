from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
    def observations_db_path(self) -> Path:
        return self.processed_dir / "observations.sqlite"

    def ensure_base_dirs(self) -> list[Path]:
        dirs = [
            self.raw_waites_dir,
            self.raw_maximo_dir,
            self.processed_dir,
        ]
        for path in dirs:
            path.mkdir(parents=True, exist_ok=True)
        return dirs

    def raw_waites_run_dir(self, run_date: str) -> Path:
        return self.raw_waites_dir / f"date={run_date}"

def get_storage_paths(data_dir: str | Path) -> StoragePaths:
    return StoragePaths(data_dir=Path(data_dir))


def get_default_fixture_dir() -> Path:
    return PROJECT_ROOT / "tests" / "fixtures"
