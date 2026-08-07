from pathlib import Path

from insy_sensor_data.storage import get_default_fixture_dir, get_storage_paths


def test_default_fixtures_are_owned_by_the_installed_package() -> None:
    fixture_dir = get_default_fixture_dir()

    assert fixture_dir.parent.name == "insy_sensor_data"
    assert fixture_dir.name == "fixtures"
    assert (fixture_dir / "waites" / "equipment.json").is_file()
    assert (fixture_dir / "maximo" / "workorders.json").is_file()


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
