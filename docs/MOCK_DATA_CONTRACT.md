# Mock Data Contract

This document defines the fixture shapes and awkward cases that should guide sprint `0.1.0`. Mock data is a first-class development mode, not a demo shortcut. It should be small, readable, and shaped like the real Waites and Maximo boundaries.

## Goals

1. Let every test run without Waites credentials, Maximo access, ODBC drivers, or plant network access.
2. Preserve the same artifact names and schemas that real source pulls will use.
3. Include enough awkward cases that later snapshot, clustering, and join logic does not quietly assume perfect data.
4. Keep fixtures inspectable by humans.
5. Make raw evidence and processed reference outputs easy to compare.

## Fixture Location

Recommended layout:

```text
tests/
  fixtures/
    waites/
      equipment.json
      installation-points.json
      readings-rms.json
      readings-impact-vue.json
      readings-temperature.json
      action-items.json
    maximo/
      workorders.json
```

The fixture files should be committed. Generated output under `data/` should stay ignored.

## Raw Artifact Location

Mock ingestion should write the same paths planned for real ingestion:

```text
data/
  raw/
    waites/
      date=YYYY-MM-DD/
        equipment.json
        installation-points.json
        readings-rms.json
        readings-impact-vue.json
        readings-temperature.json
        action-items.json
        manifest.json
```

The raw files should be close to source responses. Avoid transforming fields during raw capture.

## Processed Reference Location

Sprint `0.1.0` should also emit small reference tables:

```text
data/
  processed/
    waites/
      reference/
        equipment.csv
        installation_points.csv
        metadata.json
```

These files are used by later snapshot and asset alignment work.

## Waites Response Envelopes

Use the real-ish Waites envelope where known:

```json
{
  "list": []
}
```

The older reference code expects `equipment`, `installation-points`, and `action-items` to return an object with `list`. The reading helpers in the old code save only the nested list. For the new project, prefer storing a consistent raw envelope with `list` for every endpoint unless live API validation proves otherwise.

If the live API response differs, update this contract and the ingestion boundary before changing downstream processing.

Sprint `0.2.3` live validation confirmed that the Waites endpoints currently used by this project return a top-level object with `list` for raw capture.

## Equipment Fixture

Each equipment record should include:

```json
{
  "equipment_id": 55576,
  "asset_tree_id": 12440,
  "name": "BL - Aluminium Pinch Roll",
  "facility_id": 679,
  "customer_asset_id": "LEVF412TS"
}
```

Required fields:

- `equipment_id`
- `asset_tree_id`
- `name`
- `facility_id`
- `customer_asset_id`

Known optional live fields:

- `installation_date`
- `is_route_collector`
- `idle_threshold`
- `idle_threshold_type`
- `alerts`

Awkward cases:

- One equipment record with a blank `customer_asset_id`.
- One equipment record whose `customer_asset_id` will later match a Maximo `assetnum`.
- One equipment record whose name has extra whitespace.

## Installation Points Fixture

Each installation point record should include:

```json
{
  "installation_point_id": 201300,
  "name": "Bottom Shaft - NDE",
  "equipment_id": 55576,
  "sensor_id": 11414411,
  "facility_id": 679,
  "last_seen": "2025-07-08 13:24:18",
  "is_route_collector": 0,
  "idle_threshold": null,
  "customer_asset_id": "LEVF412TS",
  "idle_threshold_type": null,
  "alerts": []
}
```

Required fields:

- `installation_point_id`
- `name`
- `equipment_id`
- `sensor_id`
- `facility_id`
- `last_seen`
- `customer_asset_id`

Awkward cases:

- One installation point with `sensor_id` missing or null.
- One installation point with `last_seen` missing or stale.
- One installation point whose `equipment_id` is present in equipment.
- One installation point whose `equipment_id` is not present in equipment.
- One installation point with a blank `customer_asset_id`.
- One installation point with a `customer_asset_id` that disagrees with the equipment record.

## RMS Readings Fixture

Each RMS reading should include:

```json
{
  "timestamp": "2025-07-09T23:55:42Z",
  "installation_point_id": 142260,
  "axis": "x",
  "facility_id": 679,
  "acceleration": 0.87482082843781,
  "velocity": 1.7525557279587,
  "pk-pk": 81.421104431152,
  "cf": 4.8505492210388
}
```

Required fields:

- `timestamp`
- `installation_point_id`
- `axis`
- `facility_id`
- `acceleration`
- `velocity`
- `pk-pk`
- `cf`

Awkward cases:

- One sensor with all three axes: `x`, `y`, `z`.
- One sensor missing one axis.
- One duplicate timestamp for the same sensor and axis.
- One reading with a null metric value.
- One reading for an installation point not present in installation points.

## ImpactVue Readings Fixture

Each ImpactVue reading should include:

```json
{
  "timestamp": "2025-07-09T23:59:31Z",
  "installation_point_id": 76354,
  "axis": "x",
  "facility_id": 679,
  "impact_vue_acceleration": 2.0105216503143
}
```

Required fields:

- `timestamp`
- `installation_point_id`
- `axis`
- `facility_id`
- `impact_vue_acceleration`

Known optional live fields:

- `impact_vue_pk_pk`

Awkward cases:

- One high-impact outlier.
- One missing or null `impact_vue_acceleration`.
- One reading with an unexpected but source-like axis value if observed later.

## Temperature Readings Fixture

Each temperature reading should include:

```json
{
  "timestamp": "2025-07-09T23:55:44Z",
  "installation_point_id": 65992,
  "value": 35.92,
  "ambient": 33.25,
  "facility_id": 679
}
```

Required fields:

- `timestamp`
- `installation_point_id`
- `value`
- `ambient`
- `facility_id`

Awkward cases:

- One sensor with temperature readings but no RMS readings.
- One sensor with RMS readings but no temperature readings.
- One null `ambient`.
- One unusually high `value`.

## Action Items Fixture

The original refs did not include a saved action-items response, so the early mock shape was inferred from the old labeling code. Sprint `0.2.3` live shape validation confirmed that action items may also include work-order, status, sensor, equipment, node, gateway, location, report, and comment metadata.

Each action item should include at least:

```json
{
  "action_item_id": 9001,
  "action_item_type": "regular",
  "action_item_status": "active",
  "closed_at": null,
  "installation_point": {
    "installation_point_id": 201300
  }
}
```

Required fields for current use:

- `closed_at`

Useful nested fields when present:

- `installation_point.installation_point_id`
- `equipment.equipment_id`

Known optional live fields:

- `action_item_id`
- `wo_number`
- `wo_status`
- `sensor_id`
- `type`
- `status`
- `installation_point`
- `equipment`
- `description`
- `time_created`
- `updated_at`
- `urgency`
- `title`
- `created_by`
- `comments`
- `node`
- `router`
- `gateway`
- `facility_id`
- `location_id`
- `report`

Awkward cases:

- One active open item with `closed_at: null`.
- One closed item with `closed_at` populated.
- One item missing `installation_point`.
- One item whose installation point is not present in the installation point fixture.

## Maximo Work Orders Fixture

Maximo is not part of sprint `0.1.0`, but a tiny fixture should exist before join behavior is designed.

Each work order record should include:

```json
{
  "wonum": "1234567",
  "assetnum": "LEVF412TS",
  "description": "Inspect vibration on pinch roll",
  "status": "COMP",
  "worktype": "CM",
  "reportdate": "2025-07-01",
  "actfinish": "2025-07-03"
}
```

Required fields for early mock mode:

- `wonum`
- `assetnum`
- `description`
- `status`
- `worktype`
- `reportdate`
- `actfinish`

Awkward cases:

- One Maximo record that matches Waites `customer_asset_id`.
- One Maximo record with no matching Waites equipment.
- One Waites equipment record with no Maximo history.
- One open work order with `actfinish` null.

## Manifest Contract

Each mock fetch should write `manifest.json` beside the raw endpoint files.

Suggested shape:

```json
{
  "source": "mock",
  "facility_id": 679,
  "date": "2025-07-09",
  "fetched_at": "2026-07-19T00:00:00Z",
  "endpoints": [
    {
      "name": "readings-rms",
      "path": "data/raw/waites/date=2025-07-09/readings-rms.json",
      "record_count": 12,
      "params": {
        "facility[]": 679,
        "start_date": "2025-07-09T00:00:00Z",
        "end_date": "2025-07-09T23:59:59Z"
      }
    }
  ]
}
```

Required manifest fields:

- `source`
- `facility_id`
- `date`
- `fetched_at`
- `endpoints[].name`
- `endpoints[].path`
- `endpoints[].record_count`
- `endpoints[].params`

Live manifests may also include endpoint-level `status_code` and `elapsed_ms`. A failed live fetch may include an endpoint `error`; that is a hard validation failure for downstream processing.

## Validation Report Contract

Sprint `0.2.3` writes a validation report beside each raw Waites run when `waites validate` or `snapshot build` checks the raw evidence:

```text
data/raw/waites/date=YYYY-MM-DD/validation.json
```

Required validation report fields:

- `schema_version`
- `date`
- `expected_source`
- `source`
- `validated_at`
- `raw_dir`
- `manifest_path`
- `validation_path`
- `status`
- `error_count`
- `warning_count`
- `endpoints`
- `issues`

Allowed statuses:

- `valid`
- `valid_with_warnings`
- `invalid`

Warnings document tolerated awkwardness such as empty optional endpoints, null-heavy columns, missing nested action item references, or newly observed fields. Semantically nullable fields, such as `action-items.closed_at` for open action items, should not create null-heavy warnings. Hard errors include missing endpoint files, malformed envelopes, missing required fields, manifest record-count mismatches, source mismatches, and endpoint fetch errors.

## Minimum Fixture Set

For sprint `0.1.0`, target about this size:

| Fixture | Target records |
|---|---:|
| equipment | 4-6 |
| installation-points | 6-10 |
| readings-rms | 20-40 |
| readings-impact-vue | 8-16 |
| readings-temperature | 8-16 |
| action-items | 4-6 |
| maximo workorders | 3-5 |

This is enough to exercise joins and missing-data paths without turning test fixtures into a second dataset.

## Multi-Day Mock Trend Behavior

Sprint `0.2.1` keeps one compact base fixture set and applies a small date-aware overlay when mock Waites data is fetched for supported trend dates. The written raw artifacts still use the normal Waites envelope and live under the same `data/raw/waites/date=YYYY-MM-DD/` paths.

Supported mock trend dates:

| Date | Purpose |
|---|---|
| `2025-07-09` | Baseline |
| `2025-07-10` | Movement day |
| `2025-07-11` | Follow-up |

Expected sensor behaviors:

| Installation Point | Behavior |
|---|---|
| `201300` | RMS velocity, acceleration, peak-to-peak, and ImpactVue rise across all three dates |
| `201301` | RMS velocity remains stable across all three dates |
| `201303` | High vibration and temperature normalize downward after the baseline date |
| `201307` | Temperature spikes on `2025-07-10` and returns near baseline on `2025-07-11` |
| `201305` | Readings are absent on `2025-07-10` and return on `2025-07-11` |

The overlay should stay small and inspectable. Do not turn mock mode into a large synthetic data generator; its job is to prove contracts and awkward cases.

## Contract Tests

Sprint `0.1.0` should add pytest coverage that checks:

- Every fixture is valid JSON.
- Every endpoint fixture has a top-level `list`.
- Required fields are present for each record type.
- Awkward cases are represented.
- Mock fetch writes the raw artifacts and manifest.
- Processed reference tables preserve `equipment_id`, `installation_point_id`, `sensor_id`, and `customer_asset_id`.

## Update Rule

If live API evidence contradicts this document, update this document in the same commit as the code change that handles the new shape.
