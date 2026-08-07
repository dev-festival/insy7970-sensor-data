from __future__ import annotations

from pathlib import Path
import re

QUERY_DIR = Path(__file__).resolve().parent / "sql"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def read_query(name: str) -> str:
    """Read a package-owned SQL file without allowing path traversal."""
    candidate = Path(name)
    if candidate.name != name or candidate.suffix != ".sql":
        raise ValueError("Query name must be a single .sql filename")

    path = QUERY_DIR / candidate
    if not path.exists():
        raise FileNotFoundError(f"Missing Maximo query: {path}")
    return path.read_text(encoding="utf-8")


def render_query(name: str, schema: str) -> str:
    """Substitute the one validated SQL identifier used by Maximo query files."""
    normalized_schema = schema.strip()
    if not _IDENTIFIER_RE.fullmatch(normalized_schema):
        raise ValueError("MAXIMO_SCHEMA must be a valid SQL identifier")
    return read_query(name).replace("{{maximo_schema}}", normalized_schema)


def render_workorder_query(schema: str, asset_count: int) -> str:
    if asset_count < 1:
        raise ValueError("At least one Maximo asset number is required")
    placeholders = ", ".join("?" for _ in range(asset_count))
    return render_query("wo.sql", schema).replace("{{asset_placeholders}}", placeholders)
