from __future__ import annotations

from pathlib import Path
from statistics import median
from time import perf_counter
import argparse
import ctypes
import json
import os


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only service and footprint benchmark for sprint 0.6.6."
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--samples", type=int, default=5)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")

    baseline = _memory_counters()
    startup_started = perf_counter()
    from fastapi.testclient import TestClient

    from insy_sensor_data.api.main import create_app
    from insy_sensor_data.config import AppSettings

    settings = AppSettings.from_env(env_file=args.env_file)
    client = TestClient(create_app(settings))
    health = client.get("/health")
    health.raise_for_status()
    startup_seconds = perf_counter() - startup_started
    after_startup = _memory_counters()

    context = client.get("/api/context")
    context.raise_for_status()
    dates = [row["date"] for row in context.json()["dates"]]
    if not dates:
        raise RuntimeError("The configured service has no operational dates to benchmark.")
    start_date, end_date = dates[0], dates[-1]
    endpoints = {
        "context": "/api/context",
        "equipment_tree": f"/api/equipment-tree?start_date={start_date}&end_date={end_date}",
        "review": (
            f"/api/snapshot-review/{end_date}?start_date={start_date}&end_date={end_date}"
            "&metric=rms_vel&dimension=x"
        ),
        "trends": (
            f"/api/trends?start_date={start_date}&end_date={end_date}"
            "&metric=rms_vel&dimension=x"
        ),
        "cluster": f"/api/cluster-explorer?date={end_date}&metric=rms_vel&dimension=x",
        "drift": f"/api/drift-overview?start_date={start_date}&end_date={end_date}",
    }
    reads: dict[str, dict[str, object]] = {}
    for name, endpoint in endpoints.items():
        warm = client.get(endpoint)
        warm.raise_for_status()
        samples: list[float] = []
        response = warm
        for _ in range(args.samples):
            started = perf_counter()
            response = client.get(endpoint)
            samples.append(perf_counter() - started)
            response.raise_for_status()
        reads[name] = {
            "status_code": response.status_code,
            "payload_bytes": len(response.content),
            "median_seconds": round(median(samples), 6),
        }
    after_reads = _memory_counters()

    source_files = list((WORKSPACE_ROOT / "src").rglob("*.py"))
    js_files = list((WORKSPACE_ROOT / "src" / "insy_sensor_data" / "api" / "static").glob("*.js"))
    database = settings.data_dir / "processed" / "observations.sqlite"
    result: dict[str, object] = {
        "source": settings.source_mode,
        "date_count": len(dates),
        "start_date": start_date,
        "end_date": end_date,
        "startup": {
            "seconds": round(startup_seconds, 6),
            "baseline_working_set_bytes": baseline["working_set"],
            "working_set_bytes": after_startup["working_set"],
            "working_set_delta_bytes": max(
                0, after_startup["working_set"] - baseline["working_set"]
            ),
            "private_bytes": after_startup["private_bytes"],
        },
        "after_reads": {
            "working_set_bytes": after_reads["working_set"],
            "working_set_delta_from_startup_bytes": max(
                0, after_reads["working_set"] - after_startup["working_set"]
            ),
            "private_bytes": after_reads["private_bytes"],
        },
        "reads": reads,
        "source_python_file_count": len(source_files),
        "source_python_bytes": sum(path.stat().st_size for path in source_files),
        "source_python_lines": sum(
            len(path.read_text(encoding="utf-8").splitlines()) for path in source_files
        ),
        "app_javascript_file_count": len(js_files),
        "app_javascript_bytes": sum(path.stat().st_size for path in js_files),
        "database_bytes": database.stat().st_size,
    }
    if args.manifest is not None:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        result["retirement_candidates"] = {
            "file_count": manifest["file_count"],
            "file_bytes": manifest["file_bytes"],
            "database_candidate_table_rows": manifest["database"]["candidate_table_rows"],
            "legacy_snapshot_other_source_rows": manifest["database"][
                "legacy_snapshot_other_source_rows"
            ],
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _memory_counters() -> dict[str, int]:
    if os.name != "nt":
        import resource

        peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
        return {"working_set": peak, "private_bytes": peak}

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
    if not psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise OSError("GetProcessMemoryInfo failed")
    return {
        "working_set": int(counters.WorkingSetSize),
        "private_bytes": int(counters.PagefileUsage),
    }


if __name__ == "__main__":
    raise SystemExit(main())
