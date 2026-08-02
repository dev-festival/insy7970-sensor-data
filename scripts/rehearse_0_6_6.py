from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import gc
import hashlib
import json
import shutil
import sqlite3
import time

from fastapi.testclient import TestClient

from insy_sensor_data.api.main import create_app
from insy_sensor_data.config import AppSettings
from insy_sensor_data.retirement import (
    LEGACY_TABLE_CANDIDATES,
    PROTECTED_TABLES,
    apply_retirement_manifest,
    build_retirement_manifest,
    create_database_backup,
    database_integrity,
)


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rehearse 0.6.6 migration, backup, restore, and compaction on a disposable copy."
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    live = AppSettings.from_env(env_file=args.env_file)
    live_database = live.data_dir / "processed" / "observations.sqlite"
    if not live_database.is_file():
        raise FileNotFoundError(live_database)
    required_free = live_database.stat().st_size * 5 + 100 * 1024 * 1024
    free = shutil.disk_usage(WORKSPACE_ROOT).free
    if free < required_free:
        raise RuntimeError(
            f"Rehearsal needs at least {required_free} free bytes; only {free} are available."
        )

    live_sha256_before = _sha256(live_database)
    with TemporaryDirectory(
        prefix=".insy-0.6.6-rehearsal-",
        dir=WORKSPACE_ROOT,
        ignore_cleanup_errors=True,
    ) as temporary:
        root = Path(temporary)
        rehearsed = replace(live, data_dir=root / "data")
        database = rehearsed.data_dir / "processed" / "observations.sqlite"
        create_database_backup(live_database, database)
        manifest_path = root / "manifest.json"
        manifest = build_retirement_manifest(rehearsed, manifest_path)
        before = manifest["database"]["protected_table_rows"]
        result = apply_retirement_manifest(
            manifest_path,
            expected_manifest_sha256=manifest["manifest_sha256"],
            backup_path=root / "pre-retirement.sqlite",
            restore_test_path=root / "restore-rehearsal.sqlite",
            vacuum_output=root / "compacted.sqlite",
        )
        after = _counts(database, PROTECTED_TABLES)
        expected = {table: count for table, count in before.items() if count is not None}
        actual = {table: after[table] for table in expected}
        if actual != expected:
            raise RuntimeError("Protected-table counts changed during the rehearsal.")
        remaining_legacy = [
            table for table in LEGACY_TABLE_CANDIDATES if _table_exists(database, table)
        ]
        if remaining_legacy:
            raise RuntimeError(f"Legacy tables survived the rehearsal: {remaining_legacy}")

        with sqlite3.connect(root / "restore-rehearsal.sqlite") as connection:
            restored_facts = int(
                connection.execute("SELECT COUNT(*) FROM sensor_daily_facts").fetchone()[0]
            )
            restored_legacy = int(
                connection.execute("SELECT COUNT(*) FROM sensor_daily_snapshots").fetchone()[0]
            )
        context_reads = _representative_reads(rehearsed)
        compacted_counts = _counts(root / "compacted.sqlite", PROTECTED_TABLES)
        if {table: compacted_counts[table] for table in expected} != expected:
            raise RuntimeError("Compacted database protected counts do not match the source copy.")

        output = {
            "status": "complete",
            "source": live.source_mode,
            "source_schema_version": manifest["database"]["schema_version"],
            "target_schema_version": manifest["database"]["target_schema_version"],
            "live_database_unchanged": {
                "byte_count": live_database.stat().st_size,
                "integrity_check": database_integrity(live_database),
                "sha256": _sha256(live_database),
                "sha256_matches_before": _sha256(live_database) == live_sha256_before,
            },
            "copy_bytes_before": manifest["database"]["byte_count"],
            "copy_bytes_after_table_drop": database.stat().st_size,
            "compacted_bytes": result["vacuum_output"]["byte_count"],
            "compacted_bytes_saved": (
                manifest["database"]["byte_count"]
                - result["vacuum_output"]["byte_count"]
            ),
            "backup_integrity_check": result["backup"]["integrity_check"],
            "restore_integrity_check": result["restore_rehearsal"]["integrity_check"],
            "compacted_integrity_check": result["vacuum_output"]["integrity_check"],
            "restored_fixed_fact_rows": restored_facts,
            "restored_legacy_snapshot_rows": restored_legacy,
            "protected_counts_preserved": True,
            "legacy_tables_remaining": remaining_legacy,
            "representative_reads": context_reads,
            "temporary_outputs_removed_on_exit": True,
        }
        print(json.dumps(output, indent=2, sort_keys=True))
    _remove_temp_tree(root)
    return 0


def _representative_reads(settings: AppSettings) -> dict[str, int]:
    with TestClient(create_app(settings)) as client:
        context = client.get("/api/context")
        context.raise_for_status()
        dates = [row["date"] for row in context.json()["dates"]]
        start_date, end_date = dates[0], dates[-1]
        endpoints = {
            "health": "/health",
            "context": "/api/context",
            "review": (
                f"/api/snapshot-review/{end_date}?start_date={start_date}&end_date={end_date}"
            ),
            "trends": f"/api/trends?start_date={start_date}&end_date={end_date}",
            "cluster": f"/api/cluster-explorer?date={end_date}&metric=rms_vel&dimension=x",
            "drift": f"/api/drift-overview?start_date={start_date}&end_date={end_date}",
        }
        statuses = {name: client.get(path).status_code for name, path in endpoints.items()}
    if set(statuses.values()) != {200}:
        raise RuntimeError(f"Representative rehearsal reads failed: {statuses}")
    return statuses


def _counts(path: Path, tables: tuple[str, ...]) -> dict[str, int | None]:
    with sqlite3.connect(path) as connection:
        return {
            table: (
                int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                if connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (table,),
                ).fetchone()
                else None
            )
            for table in tables
        }


def _table_exists(path: Path, table: str) -> bool:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone() is not None


def _remove_temp_tree(root: Path) -> None:
    resolved = root.resolve()
    if (
        resolved.parent != WORKSPACE_ROOT.resolve()
        or not resolved.name.startswith(".insy-0.6.6-rehearsal-")
    ):
        raise RuntimeError(f"Refusing unexpected rehearsal cleanup path: {resolved}")
    for attempt in range(10):
        if not resolved.exists():
            return
        gc.collect()
        try:
            shutil.rmtree(resolved)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.25)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
