from pathlib import Path

from insy_sensor_data.storage import get_storage_paths


def test_storage_paths_create_expected_base_directories(tmp_path: Path) -> None:
    paths = get_storage_paths(tmp_path / "data")

    created = paths.ensure_base_dirs()

    assert created == [paths.raw_waites_dir, paths.raw_maximo_dir, paths.processed_dir]
    assert all(path.exists() and path.is_dir() for path in created)
    for retired in [
        "snapshots",
        "trends",
        "features",
        "clusters",
        "drift",
        "cluster_windows",
        "cluster_models",
        "cluster_model_drift",
    ]:
        assert not (paths.processed_dir / retired).exists()
