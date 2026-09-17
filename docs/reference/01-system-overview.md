# System Overview

## What is DataLoader?

DataLoader moves data from source databases into the Databricks Unity Catalog bronze layer. What
loads, how, and on what schedule is configuration in a Postgres database, not code. Adding a table
is a row, not a deployment.

- **Seven source types**: SQL Server, Oracle, PostgreSQL, Snowflake, Snowflake with key-pair auth,
  ClickHouse, S3 Iceberg
- **Six load strategies**: full, incremental, append-only, rolling window, check-and-load, chunked
  backfill
- **Scheduled by sensors**, not by a static DAG. A table becomes due, the sensor picks it up
- **Self-correcting**: four separate mechanisms catch a load that dies without reporting
- **Audited**: every run and every configuration change is recorded

---

## Key concepts

### Control plane and data plane

Configuration is separate from execution. The control plane holds intent and state; the data plane
does the work and reports back.

| Plane | Component | Responsibility |
|---|---|---|
| Control | Lakebase (Postgres) | What to load, how, when, and what happened last time |
| Orchestration | Dagster | Watches the control plane, batches the work, starts runs |
| Data | Databricks | Reads the source, writes Delta tables |
| Destination | Unity Catalog | Holds the loaded data in the bronze layer |
| Interface | dl-app | The web app people use to manage all of it |

### How scheduling works

Every 60 seconds the master sensor asks the control database which tables are due and starts runs
for them. There is no static DAG of tables. A table's `load_cron` sets when it becomes due, and
`next_load_date_time` records when that is next.

So adding a table to the system is an INSERT. Nothing is deployed, and the code location reloads
itself so the new table shows up as an asset.

### Loads are batched

One Dagster run loads **up to twelve tables**. The sensor groups the tables that are due by
source database and forced-reload flag, and each run builds one `DataLoader` that loads its tables
on parallel threads.

A run therefore covers many tables. Outcomes are recorded per table as each one finishes, so one
bad table in a batch does not fail the rest. See
[Dagster Orchestration](03-dagster-orchestration.md) for the full flow.

### Cursor tracking

For incremental strategies the loader keeps a high-water mark in `incremental_value`.

1. Read the cursor from the control row at load time.
2. Filter the source above the cursor, **minus a lookback window** so rows that committed late are
   not missed.
3. After the load, set the cursor from what was actually loaded, **capped at the moment the source
   was read** so a future-dated row cannot skip everything behind it.

The lookback and the cap are both there because the naive version loses data. See
[Load Strategies](05-load-strategies.md).

---

## System components

### 1. Lakebase, the control database

Postgres. Holds configuration and state.

| Table | Purpose |
|---|---|
| `table_control` | One row per table: source, destination, strategy, schedule, cursor, status |
| `table_control_dbconfig` | One row per source database, with Key Vault secret names |
| `historical_metadata` | One row per table per run: timings, row counts, outcome |
| `table_control_history` | Audit trail of configuration changes |
| `source_catalog` | Cached picture of a source's tables, columns, keys and indexes |
| `source_catalog_crawl` | State of each database's most recent catalog crawl |
| `dagster_reload_request` | Queue telling Dagster to reload its code location |
| `dataloader_control_vw` | The view the sensor reads to find ready tables |

Two databases: `dataloader` (production) and `dataloader_test` (pre-production). The app switches
between them with the TEST and PROD buttons in the header.

### 2. Dagster, the orchestrator

Self-hosted. Eight sensors, one schedule, four assets.

| Sensor | Interval | Purpose |
|---|---|---|
| `dataloader_master_sensor` | 60s | Find ready tables, batch them, start runs |
| `dataloader_longqueued_monitor` | 10 min | Fail rows stuck `Queued` past 60 minutes |
| `dataloader_longrunning_monitor` | 10 min | Terminate orphaned and overlong runs |
| `dataloader_failed_monitor` | 15 min | AI retry analysis on failures |
| `dataloader_reconcile_monitor` | 10 min | Fail rows whose run is gone, close orphaned history |
| `dataloader_run_canceled` | event | Fail non-terminal rows of a canceled run |
| `dataloader_run_failed` | event | Fail non-terminal rows of a failed run |
| `dagster_reload_sensor` | 5 min | Reload the code location when tables change |

Four of these ship stopped and must be enabled after a fresh deployment. See
[Dagster Orchestration](03-dagster-orchestration.md).

### 3. Databricks, the execution engine

Dagster submits one job run per batch through Dagster Pipes. The job runs
`dbx/notebooks/dataloader/dataloader_pipe.py`, which resolves secrets from Key Vault, builds one
`DataLoader`, and loads the batch on twelve threads while reporting each table's outcome back as it
lands.

### 4. Unity Catalog, the destination

Delta tables in the bronze layer. Every destination gets deletion vectors, optimized writes and auto
compaction; tables that merge on a key also get liquid clustering on that key.

### 5. dl-app, the interface

A Flask app deployed as a Databricks App. It is the thing most people actually touch: configure
databases and tables, watch loads, crawl a source's catalog, promote configuration from test to
production, and manage the Key Vault secrets behind it all. See
[Control Manager UI](07-control-manager-ui.md).

---

## End to end

```
1. Configure
   A row in table_control: source, destination, strategy, schedule.
   Usually created in dl-app, often in bulk from a crawled source catalog.

2. Become due
   next_load_date_time passes. The table appears in dataloader_control_vw.

3. Batch
   master_sensor takes up to 48 tables in due order, gates backfills to one per
   database, groups the rest by (database, full-load flag) into runs of up to 12.
   One history row per table. Every table marked Queued.

4. Start
   One Dagster run per group. The asset marks every table In Progress with the run
   URL, then submits one Databricks job through Pipes.

5. Load
   One DataLoader for the batch. Per table: read the cursor, build the windowed
   query, read the source over parallel JDBC connections, write Delta.

6. Report, per table, as it finishes
   Succeeded or Failed with a message, both run URLs, and that table's history row
   closed. A slow table does not hold up its siblings.

7. Roll up
   Succeeded, Failed, or Partial. A Partial batch fails the Dagster run for
   alerting while the tables that loaded keep their status. Tables the loader never
   reported on are failed explicitly, never left In Progress.
```

If something dies between steps 4 and 7, the reconcile monitor and the run status sensors clean up
the rows that were left behind.

---

## Database support

| Type | Connection | Notes |
|---|---|---|
| `mssql` | JDBC | Azure AD service principal supported; rowversion usable as a cursor |
| `oracle` | JDBC | Predicate compares the bare column so indexes are used; smaller fetch size |
| `postgresql` | JDBC | Standard |
| `snowflake` | Native connector | Password auth |
| `snowflake_pem` | Native connector | Key-pair auth |
| `clickhouse` | JDBC | HTTP protocol, custom type mapping |
| `s3_iceberg` | Spark and PyIceberg | No JDBC; `check_and_load` and `chunked_backfill` are rejected |

Every JDBC source gets a login timeout, a socket timeout and an application name so a hung socket
cannot hold a worker thread until the job timeout, and so the source's DBAs can see who is
connecting.

---

## Load strategies

| Strategy | Use case | Behavior |
|---|---|---|
| `full` | Small tables | One atomic Delta overwrite; no truncate, no gap for readers |
| `incremental` | Large tables with a change column | Merge on the primary key, windowed above the cursor |
| `append_only` | Immutable event data | Append above the cursor, no lookback, no dedup |
| `rolling` | Time-windowed data | Merge, and remove rows that vanished from inside the window |
| `check_and_load` | Sources that change rarely | Count first; if anything changed, reload the table |
| `chunked_backfill` | First load of a very large table | Sequential chunks, then hand over to incremental |

Deletes are a separate per-table option that works across several strategies. See
[Load Strategies](05-load-strategies.md).

---

## Quick reference

### File locations

| Path | Purpose |
|---|---|
| `dbx/functions/dataloader.py` | The loader |
| `dbx/functions/` | Focused modules: batching results, cursors, backfill, quoting, catalog crawl |
| `dbx/notebooks/dataloader/dataloader_pipe.py` | The Databricks side of a batch |
| `dagsters/sensors/` | All eight sensors |
| `dagsters/assets/dataloader_pipes_asset.py` | The batch asset and the retry asset |
| `dagsters/utils/lakebase_client.py` | Every control table query |
| `db/migrations/` | dbmate migrations, the source of truth for the schema |
| `dl-app/` | The web app |

### Commands

```bash
uvx pyrefly check --summarize-errors   # type check
uv run ruff check .                    # lint
uv run pytest -q                       # tests
dagster dev                            # run Dagster locally
```

---

## Related documentation

- [Lakebase Control Database](02-lakebase-control-database.md)
- [Dagster Orchestration](03-dagster-orchestration.md)
- [DataLoader Class](04-dataloader-class.md)
- [Load Strategies](05-load-strategies.md)
- [Troubleshooting](06-troubleshooting.md)
- [Control Manager UI](07-control-manager-ui.md)
- [Common Tasks](08-common-tasks.md)
- [Architecture Diagrams](../diagrams/02-architecture.md)
- [Sequence Diagrams](../diagrams/03-sequence-diagrams.md)
