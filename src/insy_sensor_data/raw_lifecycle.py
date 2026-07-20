from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
import gzip
import hashlib
import json
import shutil

from insy_sensor_data.artifacts import read_json, resolve_artifact_path, write_json
from insy_sensor_data.config import AppSettings
from insy_sensor_data.storage import get_storage_paths
from insy_sensor_data.waites.client import ENDPOINT_FILENAMES
from insy_sensor_data.waites.validate import ensure_waites_raw_valid


RAW_LIFECYCLE_SCHEMA_VERSION = 1
RAW_SOURCE_WAITES = "waites"


@dataclass(frozen=True)
class ArtifactDigest:
    byte_count: int
    sha256: str


def build_plain_artifact_metadata(path: Path) -> dict[str, Any]:
    digest = _file_digest(path)
    return {
        "schema_version": RAW_LIFECYCLE_SCHEMA_VERSION,
        "logical_path": path.as_posix(),
        "storage_path": path.as_posix(),
        "state": "plain",
        "compression": "none",
        "byte_count": digest.byte_count,
        "sha256": digest.sha256,
        "compressed_byte_count": None,
        "compressed_sha256": None,
    }


def compress_raw_waites(settings: AppSettings, run_date: date) -> dict[str, Any]:
    raw_dir = _waites_raw_dir(settings, run_date)
    manifest = _load_manifest(raw_dir)
    source = manifest.get("source")
    if source not in {"mock", "api"}:
        raise ValueError("Waites manifest source must be one of: api, mock")

    ensure_waites_raw_valid(settings=settings, run_date=run_date, source=source)

    compressed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for endpoint, filename in ENDPOINT_FILENAMES.items():
        logical_path = raw_dir / filename
        actual_path = resolve_artifact_path(logical_path)
        if actual_path.suffix == ".gz":
            metadata = _compressed_artifact_metadata(logical_path, actual_path)
            skipped.append({"endpoint": endpoint, "reason": "already_compressed", "path": actual_path.as_posix()})
        else:
            compressed_path = logical_path.with_name(f"{logical_path.name}.gz")
            _gzip_file(actual_path, compressed_path)
            metadata = _compressed_artifact_metadata(logical_path, compressed_path)
            actual_path.unlink()
            compressed.append(
                {
                    "endpoint": endpoint,
                    "path": compressed_path.as_posix(),
                    "byte_count": metadata["byte_count"],
                    "compressed_byte_count": metadata["compressed_byte_count"],
                }
            )
        _set_manifest_artifact(manifest, endpoint, metadata)

    _touch_lifecycle(manifest)
    _write_manifest(raw_dir, manifest)
    return {
        "source": RAW_SOURCE_WAITES,
        "date": run_date.isoformat(),
        "raw_dir": raw_dir.as_posix(),
        "manifest_path": (raw_dir / "manifest.json").as_posix(),
        "compressed_count": len(compressed),
        "skipped_count": len(skipped),
        "compressed": compressed,
        "skipped": skipped,
    }


def verify_raw_waites(settings: AppSettings, run_date: date) -> dict[str, Any]:
    raw_dir = _waites_raw_dir(settings, run_date)
    manifest = _load_manifest(raw_dir)
    issues: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []

    for endpoint, filename in ENDPOINT_FILENAMES.items():
        entry = _manifest_entry(manifest, endpoint)
        logical_path = raw_dir / filename
        if entry is None:
            _add_issue(issues, "error", endpoint, "missing_manifest_endpoint", "Endpoint is missing from manifest.")
            continue

        artifact = entry.get("artifact")
        if not isinstance(artifact, dict):
            _add_issue(issues, "error", endpoint, "missing_artifact_metadata", "Endpoint has no artifact metadata.")
            continue

        artifact_result = _verify_artifact(endpoint, logical_path, artifact, issues)
        artifacts.append(artifact_result)

    error_count = sum(1 for issue in issues if issue["level"] == "error")
    warning_count = sum(1 for issue in issues if issue["level"] == "warning")
    return {
        "source": RAW_SOURCE_WAITES,
        "date": run_date.isoformat(),
        "raw_dir": raw_dir.as_posix(),
        "manifest_path": (raw_dir / "manifest.json").as_posix(),
        "verified_at": _utc_now(),
        "status": "invalid" if error_count else "valid_with_warnings" if warning_count else "valid",
        "error_count": error_count,
        "warning_count": warning_count,
        "artifacts": artifacts,
        "issues": issues,
    }


def prune_raw_waites(
    settings: AppSettings,
    older_than_days: int,
    dry_run: bool = True,
    confirm_delete: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    if older_than_days < 0:
        raise ValueError("older_than_days must be zero or greater")
    if not dry_run and not confirm_delete:
        raise ValueError("raw prune requires --confirm-delete when --delete is used")

    storage = get_storage_paths(settings.data_dir)
    cutoff_date = (today or date.today()) - timedelta(days=older_than_days)
    candidates: list[dict[str, Any]] = []
    deleted: list[str] = []

    if storage.raw_waites_dir.exists():
        for run_dir in sorted(storage.raw_waites_dir.glob("date=*")):
            run_date = _date_from_run_dir(run_dir)
            if run_date is None or run_date >= cutoff_date:
                continue

            candidate = _prune_candidate(settings, run_date, run_dir)
            candidates.append(candidate)
            if not dry_run and candidate["delete_ready"]:
                shutil.rmtree(run_dir)
                deleted.append(run_date.isoformat())

    return {
        "source": RAW_SOURCE_WAITES,
        "older_than_days": older_than_days,
        "cutoff_date": cutoff_date.isoformat(),
        "dry_run": dry_run,
        "candidate_count": len(candidates),
        "deleted_count": len(deleted),
        "candidates": candidates,
        "deleted_dates": deleted,
    }


def refresh_waites_manifest_artifacts(settings: AppSettings, run_date: date) -> dict[str, Any]:
    raw_dir = _waites_raw_dir(settings, run_date)
    manifest = _load_manifest(raw_dir)
    for endpoint, filename in ENDPOINT_FILENAMES.items():
        logical_path = raw_dir / filename
        actual_path = resolve_artifact_path(logical_path)
        metadata = (
            _compressed_artifact_metadata(logical_path, actual_path)
            if actual_path.suffix == ".gz"
            else build_plain_artifact_metadata(actual_path)
        )
        _set_manifest_artifact(manifest, endpoint, metadata)
    _touch_lifecycle(manifest)
    _write_manifest(raw_dir, manifest)
    return manifest


def _verify_artifact(
    endpoint: str,
    logical_path: Path,
    artifact: dict[str, Any],
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    state = artifact.get("state")
    result = {
        "endpoint": endpoint,
        "logical_path": logical_path.as_posix(),
        "state": state,
        "storage_path": None,
        "byte_count": None,
        "compressed_byte_count": None,
    }

    try:
        actual_path = resolve_artifact_path(logical_path)
    except FileNotFoundError as exc:
        _add_issue(issues, "error", endpoint, "missing_artifact_file", str(exc))
        return result

    result["storage_path"] = actual_path.as_posix()
    if state == "compressed":
        if actual_path.suffix != ".gz":
            _add_issue(issues, "error", endpoint, "compression_state_mismatch", "Manifest expects gzip artifact.")
            return result
        compressed_digest = _file_digest(actual_path)
        uncompressed_digest = _gzip_uncompressed_digest(actual_path)
        result["byte_count"] = uncompressed_digest.byte_count
        result["compressed_byte_count"] = compressed_digest.byte_count
        _compare_digest(issues, endpoint, "sha256", artifact.get("sha256"), uncompressed_digest.sha256)
        _compare_digest(issues, endpoint, "byte_count", artifact.get("byte_count"), uncompressed_digest.byte_count)
        _compare_digest(
            issues,
            endpoint,
            "compressed_sha256",
            artifact.get("compressed_sha256"),
            compressed_digest.sha256,
        )
        _compare_digest(
            issues,
            endpoint,
            "compressed_byte_count",
            artifact.get("compressed_byte_count"),
            compressed_digest.byte_count,
        )
    elif state == "plain":
        if actual_path.suffix == ".gz":
            _add_issue(issues, "error", endpoint, "compression_state_mismatch", "Manifest expects plain artifact.")
            return result
        digest = _file_digest(actual_path)
        result["byte_count"] = digest.byte_count
        _compare_digest(issues, endpoint, "sha256", artifact.get("sha256"), digest.sha256)
        _compare_digest(issues, endpoint, "byte_count", artifact.get("byte_count"), digest.byte_count)
        if logical_path.with_name(f"{logical_path.name}.gz").exists():
            _add_issue(
                issues,
                "warning",
                endpoint,
                "duplicate_compressed_artifact",
                "Both plain and gzip artifacts exist for the same logical file.",
            )
    else:
        _add_issue(issues, "error", endpoint, "unknown_artifact_state", f"Unknown artifact state: {state!r}")
    return result


def _prune_candidate(settings: AppSettings, run_date: date, run_dir: Path) -> dict[str, Any]:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return {
            "date": run_date.isoformat(),
            "path": run_dir.as_posix(),
            "delete_ready": False,
            "reason": "missing_manifest",
        }

    verification = verify_raw_waites(settings=settings, run_date=run_date)
    return {
        "date": run_date.isoformat(),
        "path": run_dir.as_posix(),
        "delete_ready": verification["error_count"] == 0,
        "reason": "verified" if verification["error_count"] == 0 else "verification_failed",
        "verification_status": verification["status"],
        "error_count": verification["error_count"],
        "warning_count": verification["warning_count"],
    }


def _compressed_artifact_metadata(logical_path: Path, compressed_path: Path) -> dict[str, Any]:
    compressed_digest = _file_digest(compressed_path)
    uncompressed_digest = _gzip_uncompressed_digest(compressed_path)
    return {
        "schema_version": RAW_LIFECYCLE_SCHEMA_VERSION,
        "logical_path": logical_path.as_posix(),
        "storage_path": compressed_path.as_posix(),
        "state": "compressed",
        "compression": "gzip",
        "byte_count": uncompressed_digest.byte_count,
        "sha256": uncompressed_digest.sha256,
        "compressed_byte_count": compressed_digest.byte_count,
        "compressed_sha256": compressed_digest.sha256,
        "compressed_at": _utc_now(),
    }


def _set_manifest_artifact(manifest: dict[str, Any], endpoint: str, metadata: dict[str, Any]) -> None:
    entry = _manifest_entry(manifest, endpoint)
    if entry is None:
        return
    entry["artifact"] = metadata


def _manifest_entry(manifest: dict[str, Any], endpoint: str) -> dict[str, Any] | None:
    endpoints = manifest.get("endpoints", [])
    if not isinstance(endpoints, list):
        return None
    for entry in endpoints:
        if isinstance(entry, dict) and entry.get("name") == endpoint:
            return entry
    return None


def _load_manifest(raw_dir: Path) -> dict[str, Any]:
    manifest_path = raw_dir / "manifest.json"
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError(f"Manifest must be a JSON object: {manifest_path}")
    return manifest


def _write_manifest(raw_dir: Path, manifest: dict[str, Any]) -> None:
    write_json(raw_dir / "manifest.json", manifest)


def _touch_lifecycle(manifest: dict[str, Any]) -> None:
    manifest["raw_lifecycle"] = {
        "schema_version": RAW_LIFECYCLE_SCHEMA_VERSION,
        "updated_at": _utc_now(),
        "artifact_count": len(ENDPOINT_FILENAMES),
    }


def _waites_raw_dir(settings: AppSettings, run_date: date) -> Path:
    raw_dir = get_storage_paths(settings.data_dir).raw_waites_run_dir(run_date.isoformat())
    if not raw_dir.exists():
        raise FileNotFoundError(f"Missing raw Waites run directory: {raw_dir}")
    return raw_dir


def _date_from_run_dir(path: Path) -> date | None:
    if not path.name.startswith("date="):
        return None
    try:
        return date.fromisoformat(path.name.removeprefix("date="))
    except ValueError:
        return None


def _gzip_file(source_path: Path, output_path: Path) -> None:
    with source_path.open("rb") as source_file:
        with output_path.open("wb") as output_file:
            with gzip.GzipFile(filename="", mode="wb", fileobj=output_file, mtime=0) as gzip_file:
                shutil.copyfileobj(source_file, gzip_file)


def _file_digest(path: Path) -> ArtifactDigest:
    sha256 = hashlib.sha256()
    byte_count = 0
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            byte_count += len(chunk)
            sha256.update(chunk)
    return ArtifactDigest(byte_count=byte_count, sha256=sha256.hexdigest())


def _gzip_uncompressed_digest(path: Path) -> ArtifactDigest:
    sha256 = hashlib.sha256()
    byte_count = 0
    with gzip.open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            byte_count += len(chunk)
            sha256.update(chunk)
    return ArtifactDigest(byte_count=byte_count, sha256=sha256.hexdigest())


def _compare_digest(
    issues: list[dict[str, Any]],
    endpoint: str,
    field: str,
    expected: Any,
    actual: Any,
) -> None:
    if expected != actual:
        _add_issue(
            issues,
            "error",
            endpoint,
            f"{field}_mismatch",
            f"Manifest {field} does not match artifact.",
            {"expected": expected, "actual": actual},
        )


def _add_issue(
    issues: list[dict[str, Any]],
    level: str,
    endpoint: str,
    code: str,
    message: str,
    detail: dict[str, Any] | None = None,
) -> None:
    issue: dict[str, Any] = {
        "level": level,
        "endpoint": endpoint,
        "code": code,
        "message": message,
    }
    if detail:
        issue["detail"] = detail
    issues.append(issue)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
