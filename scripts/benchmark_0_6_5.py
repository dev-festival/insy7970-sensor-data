from __future__ import annotations

from datetime import UTC, date, datetime
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
import argparse
import ctypes
import gc
import json
import os
import subprocess
import sys

import insy_sensor_data.admin as admin
from insy_sensor_data.config import AppSettings
from insy_sensor_data.observations import connect_observation_store


FIRST_DATE = date(2025, 7, 9)
WORKSPACE_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("one", "seven"))
    args = parser.parse_args()
    if args.case:
        print(json.dumps(run_case(args.case), sort_keys=True))
        return
    results = {}
    for case in ("one", "seven"):
        completed = subprocess.run(
            [sys.executable, __file__, "--case", case],
            check=True,
            capture_output=True,
            text=True,
        )
        results[case] = json.loads(completed.stdout)
    print(json.dumps(results, indent=2, sort_keys=True))


def run_case(case: str) -> dict[str, object]:
    day_count = 1 if case == "one" else 7
    with TemporaryDirectory(
        prefix=f".insy-0.6.5-{case}-",
        dir=WORKSPACE_ROOT,
    ) as temporary:
        data_dir = Path(temporary) / "data"
        settings = AppSettings(
            data_dir=data_dir,
            source_mode="mock",
            source_timezone="America/Chicago",
            sync_start_date=FIRST_DATE,
            raw_retention_mode="release",
        )
        with closing(connect_observation_store(settings)) as connection:
            connection.execute(
                "INSERT INTO sync_control (source, start_date, current_through, "
                "source_timezone, updated_at) VALUES (?, ?, NULL, ?, ?)",
                (
                    settings.source_mode,
                    FIRST_DATE.isoformat(),
                    settings.source_timezone,
                    datetime.now(UTC).isoformat(),
                ),
            )
            connection.commit()
        database = data_dir / "processed" / "observations.sqlite"
        database_bytes_before = database.stat().st_size
        baseline_working_set = _memory_counters()["working_set"]
        peak_date_raw_bytes = 0
        peak_total_raw_bytes = 0
        real_retention = admin.apply_retention

        def measured_retention(**kwargs):
            nonlocal peak_date_raw_bytes, peak_total_raw_bytes
            run_date = kwargs["run_date"].isoformat()
            peak_date_raw_bytes = max(
                peak_date_raw_bytes,
                _tree_bytes(data_dir / "raw" / "waites" / f"date={run_date}"),
            )
            peak_total_raw_bytes = max(
                peak_total_raw_bytes,
                _tree_bytes(data_dir / "raw"),
            )
            return real_retention(**kwargs)

        admin.apply_retention = measured_retention
        now = datetime(2025, 7, 10 if day_count == 1 else 16, 12, tzinfo=UTC)
        started = perf_counter()
        if day_count == 1:
            summary = admin.run_sync(settings, run_date=FIRST_DATE, now=now)
        else:
            summary = admin.run_sync(settings, now=now)
        elapsed = perf_counter() - started
        memory = _memory_counters()
        database_bytes_after = database.stat().st_size
        result: dict[str, object] = {
            "date_count": day_count,
            "status": summary["status"],
            "elapsed_seconds": round(elapsed, 4),
            "baseline_working_set_bytes": baseline_working_set,
            "peak_working_set_bytes": memory["peak_working_set"],
            "peak_working_set_delta_bytes": max(
                0,
                memory["peak_working_set"] - baseline_working_set,
            ),
            "peak_date_raw_bytes": peak_date_raw_bytes,
            "peak_total_raw_bytes": peak_total_raw_bytes,
            "database_bytes_before": database_bytes_before,
            "database_bytes_after": database_bytes_after,
            "database_growth_bytes": database_bytes_after - database_bytes_before,
            "database_growth_bytes_per_day": round(
                (database_bytes_after - database_bytes_before) / day_count,
                2,
            ),
            "final_raw_bytes": _tree_bytes(data_dir / "raw"),
            "final_raw_file_count": _tree_file_count(data_dir / "raw"),
            "final_processed_bytes": _tree_bytes(data_dir / "processed"),
            "final_processed_file_count": _tree_file_count(data_dir / "processed"),
        }
        if day_count == 1:
            before_hash = sha256(database.read_bytes()).hexdigest()
            before_memory = _memory_counters()["working_set"]
            timings = []
            no_op_summary = None
            for _ in range(5):
                no_op_started = perf_counter()
                no_op_summary = admin.run_sync(settings, now=now)
                timings.append(perf_counter() - no_op_started)
            result["no_op_status"] = no_op_summary["status"]
            result["no_op_median_seconds"] = round(median(timings), 6)
            result["no_op_working_set_delta_bytes"] = max(
                0,
                _memory_counters()["working_set"] - before_memory,
            )
            result["no_op_database_byte_identical"] = (
                sha256(database.read_bytes()).hexdigest() == before_hash
            )
        gc.collect()
        return result


def _tree_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file()) if root.exists() else 0


def _tree_file_count(root: Path) -> int:
    return sum(1 for path in root.rglob("*") if path.is_file()) if root.exists() else 0


def _memory_counters() -> dict[str, int]:
    if os.name != "nt":
        import resource

        peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
        return {"working_set": peak, "peak_working_set": peak}

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ProcessMemoryCounters),
        ctypes.c_ulong,
    ]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    process = kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(
        process,
        ctypes.byref(counters),
        counters.cb,
    ):
        raise OSError("GetProcessMemoryInfo failed")
    return {
        "working_set": int(counters.WorkingSetSize),
        "peak_working_set": int(counters.PeakWorkingSetSize),
    }


if __name__ == "__main__":
    main()
