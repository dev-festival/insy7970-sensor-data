from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
import sqlite3

import pytest

from insy_sensor_data.clustering import engine
from insy_sensor_data.clustering.policy import ACTIVE_MODEL_POLICY
from insy_sensor_data.clustering import registry
from insy_sensor_data.clustering.registry import (
    active_model_readiness,
    build_cluster_model_grid,
    build_cluster_model_run,
    load_registered_cluster_window_view,
    rebuild_active_model_date,
)
from insy_sensor_data.config import AppSettings
from insy_sensor_data.observations import (
    connect_observation_store,
    load_sensor_daily_snapshots,
    store_sensor_daily_snapshots,
)
from insy_sensor_data.snapshots.build import build_sensor_snapshot
from insy_sensor_data.waites.fetch import fetch_waites


def test_engine_and_policy_are_deterministic_and_fully_versioned() -> None:
    rows = [
        {"a": 1.0, "b": 9.0},
        {"a": 1.5, "b": 8.0},
        {"a": 8.5, "b": 2.0},
        {"a": 9.0, "b": 1.0},
        {"a": 5.0, "b": 5.0},
    ]
    scaled = engine.standard_scale(engine.numeric_matrix(rows, ["a", "b"]), ["a", "b"])
    first = engine.kmeans(
        scaled.values,
        k=3,
        random_seed=42,
        max_iterations=100,
        tolerance=1e-6,
    )
    second = engine.kmeans(
        scaled.values,
        k=3,
        random_seed=42,
        max_iterations=100,
        tolerance=1e-6,
    )

    assert first == second
    assert engine.pca_coordinates(scaled.values, iterations=50) == engine.pca_coordinates(
        scaled.values, iterations=50
    )
    assert replace(ACTIVE_MODEL_POLICY, tolerance=1e-5).version != ACTIVE_MODEL_POLICY.version
    assert ACTIVE_MODEL_POLICY.validate_k(None) == 5
    with pytest.raises(ValueError, match="service-owned"):
        ACTIVE_MODEL_POLICY.validate_k(4)


def test_readiness_requires_current_snapshot_revision_and_policy(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    _prepare_snapshot(settings, run_date)
    model = build_cluster_model_run(settings, run_date, feature_space="x_accel")
    assert active_model_readiness(
        settings, source="mock", run_date=run_date, feature_space="x_accel"
    )["status"] == "ready"

    with connect_observation_store(settings) as connection:
        connection.execute(
            "UPDATE cluster_model_runs SET model_policy_version = 'retired' WHERE model_run_id = ?",
            (model["model_run_id"],),
        )
        connection.commit()
    policy_stale = active_model_readiness(
        settings, source="mock", run_date=run_date, feature_space="x_accel"
    )
    assert policy_stale["status"] == "stale"
    assert "policy version" in policy_stale["reason"]

    with connect_observation_store(settings) as connection:
        connection.execute(
            "UPDATE cluster_model_runs SET model_policy_version = ? WHERE model_run_id = ?",
            (ACTIVE_MODEL_POLICY.version, model["model_run_id"]),
        )
        connection.execute(
            "UPDATE snapshot_revisions SET snapshot_revision = snapshot_revision || ':changed' "
            "WHERE source = 'mock' AND source_date = ?",
            (run_date.isoformat(),),
        )
        connection.commit()
    revision_stale = active_model_readiness(
        settings, source="mock", run_date=run_date, feature_space="x_accel"
    )
    assert revision_stale["status"] == "stale"
    assert "snapshot revision" in revision_stale["reason"]


def test_targeted_rebuild_only_replaces_one_model_and_adjacent_drift(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    dates = [date(2025, 7, 9) + timedelta(days=offset) for offset in range(4)]
    for run_date in dates:
        _prepare_snapshot(settings, run_date)
    build_cluster_model_grid(
        settings,
        dates[0],
        dates[-1],
        feature_spaces=["x_accel"],
    )
    before_models = _timestamps(settings, "cluster_model_runs", "source_date")
    before_drift = _timestamps(settings, "cluster_drift_runs", "from_date || '->' || to_date")

    rebuilt_date = dates[1]
    rebuilt_rows = load_sensor_daily_snapshots(settings, rebuilt_date, source="mock")
    rebuilt_rows[0]["rms_accel_mean_x"] = float(
        rebuilt_rows[0]["rms_accel_mean_x"]
    ) + 0.001
    store_sensor_daily_snapshots(
        settings,
        rebuilt_date,
        source="mock",
        rows=rebuilt_rows,
    )
    assert active_model_readiness(
        settings,
        source="mock",
        run_date=rebuilt_date,
        feature_space="x_accel",
    )["status"] == "stale"

    summary = rebuild_active_model_date(
        settings,
        run_date=rebuilt_date,
        source="mock",
        feature_spaces=["x_accel"],
    )

    assert summary["readiness"]["status"] == "missing"
    assert summary["readiness"]["feature_readiness"][0]["status"] == "ready"
    after_models = _timestamps(settings, "cluster_model_runs", "source_date")
    after_drift = _timestamps(settings, "cluster_drift_runs", "from_date || '->' || to_date")
    assert after_models[dates[0].isoformat()] == before_models[dates[0].isoformat()]
    assert after_models[dates[2].isoformat()] == before_models[dates[2].isoformat()]
    assert after_models[dates[3].isoformat()] == before_models[dates[3].isoformat()]
    untouched_pair = f"{dates[2].isoformat()}->{dates[3].isoformat()}"
    assert after_drift[untouched_pair] == before_drift[untouched_pair]
    assert len(summary["drift_runs"]) == 2


@pytest.mark.parametrize(
    ("gap_index", "complete_pairs", "missing_pairs"),
    [(0, 2, 1), (1, 1, 2), (3, 2, 1)],
)
def test_drift_window_returns_valid_pairs_around_first_middle_or_last_gap(
    tmp_path: Path,
    gap_index: int,
    complete_pairs: int,
    missing_pairs: int,
) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    dates = [date(2025, 7, 9) + timedelta(days=offset) for offset in range(4)]
    for run_date in dates:
        _prepare_snapshot(settings, run_date)
    build_cluster_model_grid(
        settings,
        dates[0],
        dates[-1],
        feature_spaces=["x_accel"],
    )
    with connect_observation_store(settings) as connection:
        connection.execute(
            "UPDATE cluster_model_runs SET input_snapshot_revision = input_snapshot_revision || ':stale' "
            "WHERE source = 'mock' AND source_date = ? AND feature_space = 'x_accel'",
            (dates[gap_index].isoformat(),),
        )
        connection.commit()

    window = load_registered_cluster_window_view(
        settings,
        dates[0],
        dates[-1],
        source="mock",
        feature_space="x_accel",
        k=5,
    )

    assert window["status"] == "partial"
    assert window["metrics"]["complete_pair_count"] == complete_pairs
    assert window["metrics"]["missing_pair_count"] == missing_pairs
    assert len(window["aligned_drift_rows"]) == complete_pairs
    assert all(row["reason"] for row in window["missing_pairs"])


def test_insufficient_data_is_explicit_and_registered_build_creates_no_files(
    tmp_path: Path,
) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    _prepare_snapshot(settings, run_date)
    processed = settings.data_dir / "processed"
    before = {path.relative_to(processed) for path in processed.rglob("*") if path.is_file()}
    built = build_cluster_model_grid(
        settings,
        run_date,
        run_date,
        feature_spaces=["x_accel"],
    )
    after = {path.relative_to(processed) for path in processed.rglob("*") if path.is_file()}
    assert built["models_built"] == 1
    assert after == before

    with connect_observation_store(settings) as connection:
        connection.execute(
            "DELETE FROM sensor_daily_facts WHERE source = 'mock' AND source_date = ? "
            "AND installation_point_id NOT IN (SELECT installation_point_id FROM sensor_daily_facts "
            "WHERE source = 'mock' AND source_date = ? ORDER BY installation_point_id LIMIT 4)",
            (run_date.isoformat(), run_date.isoformat()),
        )
        connection.execute(
            "UPDATE snapshot_revisions SET snapshot_revision = snapshot_revision || ':small' "
            "WHERE source = 'mock' AND source_date = ?",
            (run_date.isoformat(),),
        )
        connection.commit()
    insufficient = build_cluster_model_grid(
        settings,
        run_date,
        run_date,
        feature_spaces=["x_accel"],
    )
    assert insufficient["models_insufficient_data"] == 1
    assert insufficient["models"][0]["status"] == "insufficient_data"


def test_model_and_drift_persistence_roll_back_incomplete_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    first_date = date(2025, 7, 9)
    second_date = date(2025, 7, 10)
    for run_date in (first_date, second_date):
        _prepare_snapshot(settings, run_date)

    def fail_assignments(_connection, _computed):
        raise RuntimeError("assignment persistence failed")

    monkeypatch.setattr(registry, "_insert_model_assignments", fail_assignments)
    with pytest.raises(RuntimeError, match="assignment persistence failed"):
        build_cluster_model_run(settings, first_date, feature_space="x_accel")
    assert _count(settings, "cluster_model_runs") == 0
    monkeypatch.undo()

    first_model = build_cluster_model_run(settings, first_date, feature_space="x_accel")
    second_model = build_cluster_model_run(settings, second_date, feature_space="x_accel")
    from_run = registry._find_model_run(settings, first_model["model_run_id"])
    to_run = registry._find_model_run(settings, second_model["model_run_id"])
    assert from_run is not None and to_run is not None
    drift_id = "rollback-drift"
    with pytest.raises(sqlite3.IntegrityError):
        registry._persist_complete_drift_run(
            settings,
            drift_id,
            from_run,
            to_run,
            {
                "source": "mock",
                "from_date": first_date.isoformat(),
                "to_date": second_date.isoformat(),
                "feature_space": "x_accel",
                "k": 5,
                "matched_sensor_count": 1,
                "raw_label_changed_count": 0,
                "aligned_changed_count": 0,
                "built_at": "2025-07-10T00:00:00+00:00",
            },
            [],
            [
                {
                    "installation_point_id": "1",
                    "from_cluster": "0",
                    "to_cluster": "0",
                    "aligned_to_cluster": "0",
                    "status": "matched",
                    "raw_label_changed": "false",
                    "aligned_changed": "false",
                    "distance_delta": 0.0,
                }
            ],
            [
                {"from_cluster": "0", "to_cluster": "0", "centroid_distance": 0.0},
                {"from_cluster": "0", "to_cluster": "1", "centroid_distance": 1.0},
            ],
        )
    assert _count(settings, "cluster_drift_runs", "drift_run_id = ?", (drift_id,)) == 0


def _prepare_snapshot(settings: AppSettings, run_date: date) -> None:
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    build_sensor_snapshot(settings=settings, run_date=run_date)


def _timestamps(settings: AppSettings, table: str, key_expression: str) -> dict[str, str]:
    stamp_column = "completed_at" if table == "cluster_model_runs" else "created_at"
    with connect_observation_store(settings) as connection:
        rows = connection.execute(
            f"SELECT {key_expression} AS item_key, {stamp_column} AS stamp "
            f"FROM {table} WHERE source = 'mock' AND feature_space = 'x_accel'"
        ).fetchall()
    return {str(row["item_key"]): str(row["stamp"]) for row in rows}


def _count(
    settings: AppSettings,
    table: str,
    where: str = "1 = 1",
    params: tuple[object, ...] = (),
) -> int:
    with connect_observation_store(settings) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0])
