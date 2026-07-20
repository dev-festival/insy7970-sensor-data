# Waites Vibration Monitoring - Product Spec

> **Status:** Pre-build spec - revised architecture  
> **Last updated:** 2026-07  
> **Source:** inferred from refs/ 
> **Stack:** Python, uv, FastAPI, static HTML/CSS/JS, DB2/ODBC, SQLite, Typer, Pandas, Plotly.js

---

## Table of Contents

1. [Overview](#overview)
2. [Target Architecture](#target-architecture)
3. [Product Phases](#product-phases)
4. [App Structure](#app-structure)
5. [Login & User Profiles](#login--user-profiles)
6. [Data Layer](#data-layer)
7. [Page Specs](#page-specs)
8. [File Structure](#file-structure)
9. [Open Questions / Build Notes](#open-questions--build-notes)

---

## Overview

A centrally hosted sensor + asset history app that replaces the integrates the Waites Dashboard and Maximo CMMS tools. Multiple asset condition monitoring and reliability engineers (10+ users across multiple departments) access a browser-based app on the Honda intranet. A CLI is also provided for admin, automation, diagnostics, export, and exploration workflows.

The app queries Maximo MAS9 via DB2/ODBC from the server, not from each user's machine. It also uses an API call to the waites data. It presents snapshots of multiple dimensions of X,Y,Z measurements as well as temperature per equipment, sensor location, and asset maintenance history for trending. A power feature is clustering all points using kmeans or some other clustering algorithm. 

The implementation should follow a core-first architecture:

- `app_core` owns database access, query loading, transformations, calculations, exports, clustering.
- `apps/api` exposes the core over HTTP.
- `apps/web` is a static HTML/CSS/JS app served centrally.
- `apps/cli` exposes Unix-style commands for power users and maintainers.

The project should follow a UNIX mindset, as in it is made up of tightly scoped tools that do their task well. They are minimal, composable, and promote light and efficient handling of data, and data presentation. 

### Source File Inventory

refs/ 

contains 2 old projects that have the API call to waites and some example reports. 



All of this will become parameterized SQL, Python transformations, tested report functions, and API endpoints.

---

## Target Architecture

### Deployment Model

```text
Browser users
    |
    v
Static web app: HTML/CSS/JS
    |
    v
FastAPI service
    |
    +-- app_core
    |     +-- DB2 query runner
    |     +-- cluster calculations
    |     +-- exports
    |     +--  engine
    |
    +-- SQLite user profile store
    |
    v
Maximo MAS9 via DB2/ODBC

CLI users/admins
    |
    v
uv run workload ...
    |
    v
app_core or FastAPI service
```

### Design Principles

- Do not place business logic in the browser.
- Do not place business logic in the API route handlers.
- Keep database credentials on the server.
- Keep the web app static and centrally updateable.
- Use `uv` for dependency management, locking, Python version pinning, local development, and CLI execution.
- Favor small, composable commands and modules that do one job well.

### Deployment Surfaces

| Surface | Purpose | Primary users |
|---|---|---|
| Static web app | Daily reporting and workload review | Planners, supervisors |
| FastAPI service | Central data access and report API | Web app, CLI, future integrations |
| CLI | Admin, exports, diagnostics, scheduled jobs | Owner/admin/power users |
| Shared core package | Durable business logic | All surfaces |

---

## Product Phases

### Phase 1 - 


### Phase 2 - 

---

## App Structure

### Navigation

```text

```

### Global Controls

These controls appear persistently in the web app and drive all report API calls.

| Control | Type | Behavior |
|---|---|---|


Params persist in browser state for the current session. Changing params does not auto-save; the user must explicitly click "Save as my default."

---

## Login & User Profiles

### Design Rationale

No password in Phase 1.  Guest mode ensures the tool is immediately usable by anyone without setup.

### Behavior

1. App loads with "Guest" selected by default.
2. Guest has plant-wide default params (no dept filter, last 30 days).
3. User selects their name; saved params load into the controls immediately.
4. User can adjust params for the session without saving.
5. "Save as my default" writes current controls back to SQLite.
6. Refreshing the page reloads from the saved profile, not transient browser state.

### User List Management

Maintained manually by the admin. Add/edit/remove users through CLI commands first, with an optional minimal admin UI later. No self-registration in Phase 1.

Initial user/dept defaults should be seedable from a JSON file after project structuring. The CLI can then import that seed data into SQLite during setup or deployment.

Example CLI:

```powershell

```

### SQLite Schema

**Table: `users`**

```sql

```

**Notes:**



### SQLite vs Postgres Decision

Start with SQLite:

- Single file, zero infrastructure, trivial backup.
- Sufficient for 10-20 users with infrequent writes.
- Fits a single API process or carefully controlled single-writer deployment.

Switch to Postgres if:

- Multiple API workers/processes create write contention.
- You want the user store to share infrastructure with another internal platform.
- Named simulation scenarios become a shared multi-user feature.

---

## Data Layer

### DB2 Connection

```python
# src/app_core/db.py
from functools import lru_cache
from pathlib import Path

import pandas as pd
import pyodbc


QUERY_DIR = Path(__file__).resolve().parents[2] / "queries"


def get_connection() -> pyodbc.Connection:
    return pyodbc.connect("DSN=MaximoMAS9")


def read_query(name: str) -> str:
    return (QUERY_DIR / name).read_text(encoding="utf-8")


def run_query(sql: str, params: tuple) -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql(sql, conn, params=params)


def dept_like(dept: str | None) -> str:
    if not dept or dept.upper() == "NA":
        return ""
    return f"{dept}%"


def load_all(dept_1: str | None, dept_2: str | None, start_date: str, end_date: str) -> dict[str, pd.DataFrame]:
    like_params = (dept_like(dept_1), dept_like(dept_2), start_date, end_date)
    dept_params = (dept_1, dept_2)
    forecast_params = (dept_1, dept_2, start_date, end_date)

    cm = run_query(read_query("cm_wo.sql"), like_params)
    pc = run_query(read_query("pc_wo.sql"), like_params)

    return {
        "pm": run_query(read_query("pm_wo.sql"), like_params),
        "pc": pc,
        "cm": cm,
        "bdm": run_query(read_query("bdm_wo.sql"), like_params),
        "proj": run_query(read_query("proj_wo.sql"), like_params),
        "follow_up": build_follow_up(cm, pc),
        "pm_forecast": run_query(read_query("pm_forecast.sql"), forecast_params),
        "parts": run_query(read_query("parts_orders.sql"), like_params),
        "labor": run_query(read_query("labor.sql"), dept_params),
        "persongroup": run_query(read_query("persongroup.sql"), dept_params),
    }
```

### Query Files

Each `.sql` file in `queries/` is a parameterized DB2 query using positional ODBC parameter markers (`?`). 

| File | Source connection | Key filter logic |
|---|---|---|


### Derived DataFrames


```powerquery

```


### Caching Strategy

- Server-side cache per unique parameter combination.
- Default TTL: 5 minutes.
- Refresh button bypasses cache for the selected parameter set.
- CLI supports `--refresh` to bypass cache.
- User profile reads are not cached unless later profiling proves it is needed.

### Mock Data Mode

The core package should support fixture-backed mock data. This enables local development, automated tests, and UI work without a DB2 connection.

```powershell

```

---

## Page Specs


---


#### Export


#### UX Note on Drag-and-Drop


---

## File Structure

```text
pyproject.toml
uv.lock
.python-version
.env.example
.gitignore
README.md
SPEC.md

src/
  app_core/
    __init__.py
    config.py
    data.py
    db.py
    mock_data.py
    query_loader.py
    users.py
    schemas.py
    reporting.py

apps/
  api/
    main.py
    routes/
      health.py
      users.py
      reports.py
      exports.py
  cli/
    main.py
  web/
    index.html
    app.js
    styles.css

queries/


tests/
  fixtures/
    mock_data/
  unit/
  integration/
  e2e/

app.db
```

**`.gitignore` should include:**

```text
.env
.venv/
app.db
__pycache__/
*.pyc
.pytest_cache/
dist/
```

---

## Open Questions / Build Notes

These are items to revisit during build and test. They are not blockers for starting.

### Data / Queries

### Auth / Users

### UI / UX

### CLI


### Infrastructure

---

*Spec version: rev1 - revised to uv + FastAPI + static web + CLI architecture.*
