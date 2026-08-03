from __future__ import annotations

from pathlib import Path
import argparse
import json
from dataclasses import replace

from insy_sensor_data.config import AppSettings
from insy_sensor_data.retirement import (
    activate_compacted_database,
    apply_retirement_manifest,
    build_retirement_manifest,
    prepare_retirement_checkpoint,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run-first legacy retirement for sprint 0.6.6.",
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--manifest", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--activate", action="store_true")
    parser.add_argument("--confirm-manifest-sha256")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--restore-test", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--approval-bundle", type=Path)
    parser.add_argument("--confirm-approval-sha256")
    parser.add_argument("--vacuum-output", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--apply-result", type=Path)
    parser.add_argument("--confirm-result-sha256")
    parser.add_argument("--confirm-vacuum-sha256")
    parser.add_argument("--displaced-database", type=Path)
    parser.add_argument("--activation-result", type=Path)
    args = parser.parse_args()

    if args.activate:
        if (
            args.apply_result is None
            or not args.confirm_result_sha256
            or not args.confirm_vacuum_sha256
            or args.displaced_database is None
            or args.activation_result is None
        ):
            parser.error(
                "--activate requires --apply-result, --confirm-result-sha256, "
                "--confirm-vacuum-sha256, --displaced-database, and --activation-result"
            )
        result_path = args.activation_result.resolve()
        apply_payload = json.loads(args.apply_result.read_text(encoding="utf-8"))
        processed_dir = Path(apply_payload["database_after"]["absolute_path"]).resolve().parent
        if _is_within(result_path, processed_dir):
            parser.error("--activation-result must be outside data/processed")
        if result_path in {args.apply_result.resolve(), args.displaced_database.resolve()}:
            parser.error("--activation-result must differ from activation inputs and outputs")
        if result_path.exists():
            parser.error(f"--activation-result already exists: {result_path}")
        try:
            result = activate_compacted_database(
                args.apply_result,
                expected_apply_result_sha256=args.confirm_result_sha256,
                expected_vacuum_sha256=args.confirm_vacuum_sha256,
                displaced_database_path=args.displaced_database,
            )
        except Exception as exc:
            result = {
                "operation": "checkpoint_b_activation",
                "status": "failed",
                "apply_result_path": args.apply_result.resolve().as_posix(),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            _write_result(result_path, result)
            print(json.dumps(result, sort_keys=True))
            return 1
        _write_result(result_path, result)
        print(json.dumps(result, sort_keys=True))
        return 0

    if args.manifest is None:
        parser.error("--manifest is required for dry-run, preparation, and apply modes")

    if args.prepare:
        if (
            not args.confirm_manifest_sha256
            or args.backup is None
            or args.restore_test is None
            or args.archive is None
            or args.approval_bundle is None
        ):
            parser.error(
                "--prepare requires --confirm-manifest-sha256, --backup, --restore-test, "
                "--archive, and --approval-bundle"
            )
        result = prepare_retirement_checkpoint(
            args.manifest,
            expected_manifest_sha256=args.confirm_manifest_sha256,
            backup_path=args.backup,
            restore_test_path=args.restore_test,
            artifact_archive_path=args.archive,
            approval_bundle_path=args.approval_bundle,
        )
        output = {
            "status": result["status"],
            "manifest": result["manifest"],
            "approval_bundle_path": result["approval_bundle_path"],
            "approval_bundle_sha256": result["approval_bundle_sha256"],
            "backup": result["backup"],
            "restore_rehearsal": result["restore_rehearsal"],
            "artifact_archive": result["artifact_archive"],
            "approved_cleanup": result["approved_cleanup"],
        }
        print(json.dumps(output, sort_keys=True))
        return 0

    if args.apply:
        if (
            not args.confirm_manifest_sha256
            or args.approval_bundle is None
            or not args.confirm_approval_sha256
            or args.vacuum_output is None
            or args.result is None
        ):
            parser.error(
                "--apply requires --confirm-manifest-sha256, --approval-bundle, "
                "--confirm-approval-sha256, --vacuum-output, and --result; "
                "live use also requires separate operator approval"
            )
        result_path = args.result.resolve()
        manifest_payload = json.loads(args.manifest.read_text(encoding="utf-8"))
        processed_dir = Path(manifest_payload["processed_dir"]).resolve()
        if _is_within(result_path, processed_dir):
            parser.error("--result must be outside data/processed")
        if result_path in {
            args.manifest.resolve(),
            args.approval_bundle.resolve(),
            args.vacuum_output.resolve(),
        }:
            parser.error("--result must differ from apply inputs and outputs")
        if result_path.exists():
            parser.error(f"--result already exists: {result_path}")
        try:
            result = apply_retirement_manifest(
                args.manifest,
                expected_manifest_sha256=args.confirm_manifest_sha256,
                approval_bundle_path=args.approval_bundle,
                expected_approval_bundle_sha256=args.confirm_approval_sha256,
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
        print(json.dumps(result, sort_keys=True))
        return 0

    else:
        settings = AppSettings.from_env(env_file=args.env_file)
        if args.data_dir is not None:
            settings = replace(settings, data_dir=args.data_dir)
        result = build_retirement_manifest(settings, args.manifest)
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


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
