# Dagster Orchestration

Dagster decides what loads and when. It reads the control database, groups the tables that are due
into batches, and starts one Databricks run per batch.

---

## Architecture

```
Lakebase                     Dagster (self-hosted)              Databricks
--------                     ---------------------              ----------
dataloader_control_vw  --->  master_sensor (60s)
                               gate backfills
                               select up to 48 tables
                               group into runs of 12      --->  one job run per batch
                                                                  one DataLoader
                                                                  12 table threads
table_control          <---  status writes               <---  per-table outcome
historical_metadata    <---  one row per table
```

Dagster is self-hosted. Run concurrency is set in `dagster.yaml`:
`QueuedRunCoordinator` with `max_concurrent_runs: 64` and 8 sensor threads.

| Piece | Count | Where |
|---|---|---|
| Sensors | 8 | `dagsters/sensors/` |
| Schedules | 1 | `dagsters/assets/ingestion_logs_asset.py` |
| Assets | 4 named, plus the bronze external asset list | `dagsters/assets/` |
| Resources | 4 | `dagsters/definitions.py` |

Every sensor and asset name carries the environment as a suffix, `_dev` or `_prod`, from
`DAGSTER_ENVIRONMENT` (default `dev`).

---

## Loads are batched

A run carries a batch of tables, not a single table. The sensor groups the tables that are due, and
each run builds one `DataLoader` that loads up to twelve of them on twelve threads.

Batching is what keeps the Dagster host stable. With `max_concurrent_runs: 64` on an 8 core driver,
one run per table meant a busy tick could ask for dozens of concurrent runs and put up to 64 driver
processes on one machine. Grouping the work puts the concurrency inside a run, where it is threads
against one JDBC connection pool rather than processes against one host.

### What a tick does

1. **Read the ready tables.** `get_control_tables()` selects from the view
   `control.dataloader_control_vw`, ordered by `next_load_date_time`, then `last_modified_at`.

2. **Gate the backfills.** If any ready table is a `chunked_backfill`, at most one is allowed per
   source database. The rest are deferred with a log line. A backfill can run for hours and
   saturate the source on its own.

3. **Select the tables.** `select_batch` walks whole runs in due order until it has covered
   `BATCH_SIZE` tables. The run that crosses the limit is taken whole, so a tick can overshoot
   slightly. That is deliberate: splitting a run to hit an exact count would put two halves of one
   database's work into two jobs.

4. **Group into runs.** The grouping key is `(db_config_key, load_full, solo)`.

   | Part of the key | Why it is in the key |
   |---|---|
   | `db_config_key` | One `DataLoader` binds to one source database in its constructor |
   | `load_full` | `DataLoader` takes one `full_load` flag and applies it to every table |
   | `solo` | `chunked_backfill` runs alone, one table per run |

   Each group is then chunked at `TABLES_PER_RUN`, which matches the loader's worker count.

5. **Insert metadata once.** One multi-row INSERT creates every table's `historical_metadata` row
   and returns the ids keyed by `control_key`.

6. **Queue once.** One UPDATE marks every selected table `Queued`.

7. **Yield one RunRequest per group**, carrying the shared database fields once and a `tables` list
   of per-table specs.

### Tunables

| Name | Default | Env var | Meaning |
|---|---|---|---|
| Sensor interval | 60s | | How often the master sensor looks for work |
| `BATCH_SIZE` | 48 | `DATALOADER_SENSOR_BATCH_SIZE` | Tables covered per tick, a floor rather than a cap |
| `TABLES_PER_RUN` | 12 | `DATALOADER_TABLES_PER_RUN` | Tables in one run, matched to the loader's threads |
| Run timeout | 3h | | Per Databricks job run |
| Run timeout, solo backfill | 24h | | A batch holding a `chunked_backfill` |

### Run config shape

The config describes one batch. Shared connection fields sit above the per-table list so they are
sent once.

```json
{
  "db_config_key": "EnertiaProd",
  "db_type": "mssql",
  "db_config_value": { "host": "...", "port": "...", "service_name": "...", "user": "...", "password": "..." },
  "db_kv_scope": "azure-keyvault",
  "load_full": false,
  "control_schema": "control",
  "run_timeout_seconds": 10800,
  "tables": [
    { "config_id": 412, "control_key": "9f2c...", "source_schema_name": "dbo", "source_table_name": "rvCompany" },
    { "config_id": 418, "control_key": "a731...", "source_schema_name": "dbo", "source_table_name": "rvBudget" }
  ]
}
```

`incremental_value` is not in the config. The loader reads the cursor from the control row itself,
so a value that moved between the sensor tick and the job starting is still correct.

### Run tags

`db_config_key`, `table_count`, and `tables` (source names, joined and truncated to 200
characters). There is no `config_id` or `control_key` tag, because a run covers many tables.
Anything needing the table list reads `config.tables` from the run config.

---

## How a batch reports per-table results

A batch would be useless if one bad table failed the other eleven, so outcomes are recorded per
table as they land.

1. The asset marks every table in the batch `In Progress` with the Dagster run URL **before**
   submitting the job. That is what makes a batch that never starts recoverable.
2. The pipe notebook marks them `In Progress` again with both run URLs, builds one `DataLoader`,
   and runs `process_tables` on a worker thread.
3. The main thread polls every 15 seconds. Each poll asks which tables the loader has finished with
   and nothing has recorded yet. For each one it writes `Succeeded` or `Failed` with the message,
   and closes that table's `historical_metadata` row straight away, so a slow table does not hold
   the rest of its batch at `In Progress`.
4. After the worker joins, every table with no result is resolved. Failure wins over success if a
   table appears in both lists, and **silence counts as failure**: "The loader returned no result
   for this table. It was part of a batch that finished, so it was neither loaded nor reported as
   failed."
5. The batch rolls up to `Succeeded`, `Failed` or `Partial`. A `Partial` batch raises at the end of
   the pipe, so the Dagster run goes red for alerting while the tables that did load keep their
   `Succeeded` status.

Results are matched to tables by `control_key`, not by table name, because cloned configs can share
a destination name. A result record with no `control_key` is ignored on purpose: the control table
update error record describes the batch, not any one table.

### When a whole batch dies

Four layers, in the order they catch it.

| Layer | Catches | Behavior |
|---|---|---|
| Inside the pipe | The batch raised after starting | Every unresolved table gets the batch error; already terminal tables keep their own outcome |
| Asset fallback | The job submission itself raised | Re-reads each table's status and overwrites only rows that are not already `Succeeded` or `Failed` |
| Run status sensors | The run was canceled or died outside the asset body | Reads `config.tables[*].config_id` from the run config and fails only non-terminal rows |
| Reconcile monitor | Everything else, including an unreadable run config | Works from `table_control.last_dagster_run` |

### Lineage survives batching

The batch reports one asset materialization carrying `table_results` as JSON. The asset parses it
and emits one bronze `AssetMaterialization` per table, so per-table lineage in Dagster is unchanged.
The blob event export is one file per batch, not per table.

---

## Sensors and schedules

Four sensors ship stopped and must be enabled in the Dagster UI after a fresh deployment. Four ship
running.

| Name | Type | Interval | Default | Purpose |
|---|---|---|---|---|
| `dataloader_master_sensor` | sensor | 60s | **Stopped** | Find ready tables, batch them, start runs |
| `dataloader_longqueued_monitor` | sensor | 10 min | **Stopped** | Fail rows stuck `Queued` past 60 minutes |
| `dataloader_longrunning_monitor` | sensor | 10 min | **Stopped** | Terminate orphaned and overlong runs, then fail the rows |
| `dataloader_failed_monitor` | sensor | 15 min | **Stopped** | AI retry analysis for failed rows under 3 retries |
| `dagster_reload_sensor` | sensor | 5 min | Running | Reload the code location when tables are added or removed |
| `dataloader_reconcile_monitor` | sensor | 10 min | Running | Fail rows left `In Progress` by a run that is gone, close orphaned history rows |
| `dataloader_run_canceled` | run status | event | Running | Fail non-terminal rows of a canceled run |
| `dataloader_run_failed` | run status | event | Running | Fail non-terminal rows of a failed run |
| `ingestion_logs_schedule` | schedule | hourly | Running | Merge blob event logs into `admin.ingestion_logs` |

> `lakebase_client.py` still carries two methods for generating staging models,
> `get_new_landing_tables` and `mark_staging_model_processed`. No sensor calls either one. See
> [Known gaps](02-lakebase-control-database.md#known-gaps).

### longqueued_monitor

Finds rows `Queued` longer than 60 minutes and marks them `Failed` with
`Cancelled: queued for N minutes (threshold: 60 min)`. Despite the word in the message it does not
cancel the Dagster run; it only corrects the control row.

### longrunning_monitor

Two phases.

1. **Orphan detection.** Runs where Databricks finished but the Pipes signal never arrived, older
   than 20 minutes, are terminated.
2. **Overlong runs.** Rows `In Progress` past 180 minutes have their Dagster run terminated and are
   then marked `Failed`.

### failed_monitor

Every 15 minutes, finds `Failed` rows with fewer than 3 retries and yields a `RunRequest` for
`dataloader_retry_analysis`.

### reconcile_monitor

Added after the September 2026 outage, when rows sat `In Progress` for days because the runs that
owned them had been killed. Marks a row `Failed` once it has been `In Progress` for more than 20
minutes and the Dagster run recorded against it is missing or already finished. A second pass closes
`historical_metadata` rows that never received a final status. This is the backstop that does not
depend on the run config being readable.

### run_canceled and run_failed

Dagster run status sensors on `CANCELED` and `FAILURE`. Both read the batch's `config_id` list from
the run config, falling back to the pre-batching `config_id` tag for old runs, and mark only tables
that are not already terminal. A partly successful batch is left alone.

### dagster_reload_sensor

A statement-level trigger on `table_control` queues a row in `control.dagster_reload_request` when
tables are inserted, deleted, or have their destination changed. This sensor claims those rows and
calls the webserver's GraphQL `ReloadCode`, so new tables appear as assets without a deploy.

It only works when the code location runs under `dagster code-server start`. Under
`dagster api grpc` the reload handler is a no-op that logs a warning.

---

## Assets

### dataloader_table_load

Loads a **batch** of tables through Databricks Pipes. Config is `DataloaderBatchConfig`, holding the
shared database fields plus a list of `DataloaderTableSpec`. An empty batch raises rather than
quietly doing nothing.

The asset key is `["dataloader_{env}", "table_load"]`. The job timeout comes from the run config, so
a solo backfill gets 24 hours and everything else gets 3.

The cluster id is not an environment variable. It comes from the Key Vault secret
`databricks-dataloader-cluster-id`.

### dataloader_retry_analysis

Sends a failed table's error to an LLM for classification, then either resets it for retry or marks
it for human review. 300 second timeout.

### lakebase_connection_test

Proves the Lakebase resource can connect. Useful as a first check after a deployment.

### ingestion_logs_ingest

Driven by `ingestion_logs_schedule` on the hour. Merges the JSON event logs written to blob storage
into `{catalog}.admin.ingestion_logs`.

---

## Resources

| Resource | Purpose |
|---|---|
| `lakebase` | Connection pool and every control table read and write |
| `pipes_databricks` | Submits and supervises the Databricks job |
| `databricks_rest` | Direct REST calls, used for run termination and status checks |
| `dbt` | dbt project resource |

### Lakebase client methods

| Method | Purpose |
|---|---|
| `get_control_tables()` | Ready tables from `dataloader_control_vw` |
| `get_table_by_config_id(config_id)` | One row by id |
| `get_table_details(control_key)` | One row by control key |
| `databases_with_running_backfills()` | Keys to gate in this tick |
| `insert_batch_metadata(tables)` | One history row per table, returns ids by control key |
| `batch_update_status_queued(config_ids)` | Queue a whole batch in one statement |
| `update_control_status(config_id, status, message)` | Per-table status write |
| `update_run_tracking(config_id, dagster_url, databricks_url)` | Store both run URLs |
| `update_metadata(metadata_id, ...)` | Close one history row with results |
| `get_open_history_rows()`, `close_open_history()`, `close_history_row()` | Reconciliation |
| `get_in_progress_jobs()` | Rows the monitors examine |
| `claim_reload_requests()`, `release_reload_requests()` | Code location reload queue |
| `increment_retry_count()`, `reset_retry_count()` | Retry bookkeeping |
| `reset_for_retry(config_id, reason)` | Clear status and make the row due now |
| `mark_for_review(config_id, reason)` | Flag for a human |

---

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DAGSTER_ENVIRONMENT` | `dev` | `dev` or `prod`; suffixes every sensor and asset name |
| `DAGSTER_WEBSERVER_URL` | `http://localhost:3000` | Base for the run URLs written to `table_control` |
| `LAKEBASE_HOST` | | Control database host |
| `LAKEBASE_PORT` | 5432 | Control database port |
| `LAKEBASE_DATABASE` | | `dataloader` or `dataloader_test` |
| `LAKEBASE_USERNAME` | | Control database user |
| `LAKEBASE_PASSWORD` | | Control database password |
| `DATALOADER_SENSOR_BATCH_SIZE` | 48 | Tables covered per sensor tick |
| `DATALOADER_TABLES_PER_RUN` | 12 | Tables per Dagster run |

The Databricks host, token and dataloader cluster id are read from Key Vault, not the environment.

---

## Bidirectional traceability

Both run URLs are written to `table_control` for every table in a batch, so you can get from a row
in the control database to the Dagster run and to the Databricks job run, and back.

| Column | Points at |
|---|---|
| `last_dagster_run` | `{DAGSTER_WEBSERVER_URL}/runs/{run_id}` |
| `last_databricks_run` | The Databricks job run URL |

Both are set to NULL when a table is marked `Queued`, so a stale link never survives into the next
run.

---

## File locations

| Path | Holds |
|---|---|
| `dagsters/definitions.py` | Assembles sensors, assets, jobs and resources |
| `dagsters/sensors/` | All eight sensors |
| `dagsters/sensors/dataloader_master_sensor.py` | Batching and run selection |
| `dagsters/assets/dataloader_pipes_asset.py` | The batch asset and the retry asset |
| `dagsters/assets/ingestion_logs_asset.py` | The hourly log ingestion job and schedule |
| `dagsters/assets/external_assets.py` | Bronze external asset list |
| `dagsters/utils/lakebase_client.py` | Every control table query |
| `dagsters/utils/run_identity.py` | Reads config ids out of a run config |
| `dbx/functions/batch_results.py` | Attributing a batch's results to individual tables |
| `dbx/notebooks/dataloader/dataloader_pipe.py` | The Databricks side of a batch |

---

## Related documentation

- [System Overview](01-system-overview.md)
- [Lakebase Control Database](02-lakebase-control-database.md), including `dataloader_control_vw`
- [DataLoader Class](04-dataloader-class.md)
- [Load Strategies](05-load-strategies.md)
- [Troubleshooting](06-troubleshooting.md)
- [Sequence Diagrams](../diagrams/03-sequence-diagrams.md)
