from __future__ import annotations

from pathlib import Path
import argparse
import json
from dataclasses import replace

from insy_sensor_data.config import AppSettings
from insy_sensor_data.retirement import (
    apply_retirement_manifest,
    build_retirement_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run-first legacy retirement for sprint 0.6.6.",
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-manifest-sha256")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--restore-test", type=Path)
    parser.add_argument("--vacuum-output", type=Path)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()

    if args.apply:
        if (
            not args.confirm_manifest_sha256
            or args.backup is None
            or args.restore_test is None
            or args.result is None
        ):
            parser.error(
                "--apply requires --confirm-manifest-sha256, --backup, --restore-test, "
                "and --result; "
                "live use also requires separate operator approval"
            )
        result_path = args.result.resolve()
        manifest_payload = json.loads(args.manifest.read_text(encoding="utf-8"))
        processed_dir = Path(manifest_payload["processed_dir"]).resolve()
        if result_path == processed_dir or processed_dir in result_path.parents:
            parser.error("--result must be outside data/processed")
        reserved_paths = {
            args.manifest.resolve(),
            args.backup.resolve(),
            args.restore_test.resolve(),
            *([args.vacuum_output.resolve()] if args.vacuum_output is not None else []),
        }
        if result_path in reserved_paths:
            parser.error("--result must differ from the manifest and database outputs")
        if result_path.exists():
            parser.error(f"--result already exists: {result_path}")
        try:
            result = apply_retirement_manifest(
                args.manifest,
                expected_manifest_sha256=args.confirm_manifest_sha256,
                backup_path=args.backup,
                restore_test_path=args.restore_test,
                vacuum_output=args.vacuum_output,
            )
        except Exception as exc:
            result = {
                "operation": "legacy_retirement",
                "status": "failed",
                "manifest_path": args.manifest.resolve().as_posix(),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            _write_result(result_path, result)
            print(json.dumps(result, sort_keys=True))
            return 1
        _write_result(result_path, result)
    else:
        settings = AppSettings.from_env(env_file=args.env_file)
        if args.data_dir is not None:
            settings = replace(settings, data_dir=args.data_dir)
        result = build_retirement_manifest(settings, args.manifest)
    if args.apply:
        output = result
    else:
        output = {
            "status": result["status"],
            "manifest_path": result["manifest_path"],
            "manifest_sha256": result["manifest_sha256"],
            "source": result["source"],
            "file_count": result["file_count"],
            "file_bytes": result["file_bytes"],
            "database": result["database"],
        }
    print(json.dumps(output, sort_keys=True))
    return 0


def _write_result(path: Path, result: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
