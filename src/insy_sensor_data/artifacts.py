from __future__ import annotations

from pathlib import Path
from typing import Any
import csv
import gzip
import json


def read_json(path: Path) -> dict[str, Any]:
    artifact_path = resolve_artifact_path(path)
    if artifact_path.suffix == ".gz":
        with gzip.open(artifact_path, "rt", encoding="utf-8") as json_file:
            return json.load(json_file)
    return json.loads(artifact_path.read_text(encoding="utf-8"))


def resolve_artifact_path(path: Path) -> Path:
    if path.exists():
        return path

    if path.suffix != ".gz":
        compressed_path = path.with_name(f"{path.name}.gz")
        if compressed_path.exists():
            return compressed_path

    raise FileNotFoundError(f"Missing required artifact: {path}")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing required artifact: {path}")
    with path.open(newline="", encoding="utf-8") as csv_file:
        return list(csv.DictReader(csv_file))


def write_csv_rows(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in fieldnames})


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True)
    return value
