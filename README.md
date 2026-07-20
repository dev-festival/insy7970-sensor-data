# INSY Sensor Data

Lightweight vibration monitoring service for Waites sensor data and Maximo maintenance history.

The project is intentionally built around small, composable tools. The CLI is the canonical automation surface, FastAPI is the canonical service surface, and both should use the same core modules.

## Current Sprint

Sprint `0.0.0` establishes the project and service foundation:

- uv-managed Python package
- Typer CLI entry point
- FastAPI app factory and `/health`
- static browser app shell
- `.env.example` configuration contract
- pytest harness

Sensor ingestion, snapshots, clustering, and Maximo integration begin in later sprints.

## Requirements

- Python 3.13
- uv

## Setup

```powershell
uv sync --dev
Copy-Item .env.example .env
```

Edit `.env` for local values. Keep `.env` out of Git.

## CLI

```powershell
uv run sensor-data --help
uv run sensor-data health
uv run sensor-data serve --source mock
```

The health command prints JSON so it can be used by scripts.

## FastAPI Service

Start the local service:

```powershell
uv run sensor-data serve --source mock
```

Then open:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/docs`

## Source API

The first external API is the Waites data API.

- Base URL placeholder: `https://data.api.waites.net/v1_1`
- Documentation link placeholder: add official Waites API docs link here when available.

Example request shape planned for sprint `0.1.0`:

```text
GET /readings/rms?facility[]=679&start_date=YYYY-MM-DDT00:00:00Z&end_date=YYYY-MM-DDT23:59:59Z
```

The response contains timestamped sensor readings keyed by `installation_point_id`, axis, facility, and metric values. Raw responses will be saved under `data/raw/`; processed outputs will be saved under `data/processed/`.

## Tests

```powershell
uv run pytest
```

Tests should run in mock mode without API keys, ODBC drivers, DB2 access, or plant network access.

## Design Docs

- [CLI Reference](docs/CLI.md)
- [Grand Design](docs/GRAND_DESIGN.md)
- [Sprint Plan](docs/sprints/README.md)
