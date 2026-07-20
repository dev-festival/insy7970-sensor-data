from pathlib import Path

from insy_sensor_data.storage import get_storage_paths


def test_storage_paths_create_expected_base_directories(tmp_path: Path) -> None:
    paths = get_storage_paths(tmp_path / "data")

    created = paths.ensure_base_dirs()

    assert paths.raw_waites_dir in created
    assert paths.raw_maximo_dir in created
    assert paths.snapshots_dir in created
    assert paths.trends_dir in created
    assert paths.clusters_dir in created
    assert paths.drift_dir in created
    assert all(path.exists() and path.is_dir() for path in created)
