# Troubleshooting

Diagnosis when something is wrong: a table that never runs, a row stuck In Progress, a batch that
died, a load that finished but loaded the wrong rows. Routine work lives in
[Common Tasks](08-common-tasks.md).

One fact shapes everything below. A Dagster run loads a **batch** of up to 12 tables that share a
source database, so failures arrive in groups and a single run holds several rows in
`table_control`. Read [Dagster Orchestration](03-dagster-orchestration.md) first if that is news.

---

## Quick diagnosis

Three queries and a link. Start here before opening anything.

```sql
-- What the fleet is doing right now
SELECT last_status, COUNT(*)
FROM control.table_control
WHERE is_active = true
GROUP BY last_status;

-- The last ten failures, with the run that produced them
SELECT source_table_name,
       db_config_key,
       last_status_message,
       last_status_date_time,
       last_dagster_run
FROM control.table_control
WHERE last_status = 'Failed'
ORDER BY last_status_date_time DESC
LIMIT 10;

-- Anything In Progress longer than the reconcile monitor's own threshold
SELECT source_table_name,
       last_dagster_run,
       EXTRACT(EPOCH FROM (
           CURRENT_TIMESTAMP - COALESCE(last_load_date_time, last_status_date_time)
       ))/60 AS minutes_in_progress
FROM control.table_control
WHERE last_status = 'In Progress'
  AND COALESCE(last_load_date_time, last_status_date_time)
      < CURRENT_TIMESTAMP - INTERVAL '20 minutes'
ORDER BY 3 DESC;
```

If the second query returns several tables from one `db_config_key` at the same second, they share
a `last_dagster_run`. That is one batch, one failure, not five problems.

### Log locations

| What you want | Where it is |
|---|---|
| Why a run died | Dagster UI, Runs, then the run's logs |
| Why nothing launched | Dagster UI, Sensors, the tick log for `dataloader_master_sensor`. Deferred backfills are logged by name |
| The real stack trace | Databricks job run, linked from `table_control.last_databricks_run` |
| One table's summary | `control.table_control.last_status_message`. A summary, not the trace |
| One execution's record | `control.historical_metadata.log_message`, joined by `control_key` |
| Long-term load events | `{catalog}.admin.ingestion_logs`, merged hourly from the JSON events the loader writes to blob storage |
| The same thing with a UI | dl-app Operations, Runs, and the status views |

---

## Reading a batch failure

A batch reports per table as each table lands, then rolls up.

| Roll-up | Meaning | What the Dagster run does |
|---|---|---|
| `Succeeded` | Every table loaded | Green |
| `Failed` | No table loaded | Red |
| `Partial` | Some loaded, some did not | Red. The pipe raises at the end so alerting sees it |

A `Partial` batch is the one that confuses people. The run is red, but the tables that loaded are
sitting at `Succeeded` and are correct. Only the failed rows need attention. Nothing downstream
reverses a table that loaded because a sibling failed.

Three messages are worth recognising on sight.

"The loader returned no result for this table. It was part of a batch that finished, so it was
neither loaded nor reported as failed." The batch ran to completion and never mentioned this table.
Silence is recorded as failure on purpose; leaving the row In Progress is what stranded tables
during the September 2026 outage. Look at the Databricks run's logs for what the loader was doing
when it skipped the table, then reset the row.

"Dagster run \<id\> ended with status CANCELED/FAILURE before the load reported a result." Written
by `dataloader_run_canceled` or `dataloader_run_failed`. Someone canceled the run, or it died
outside the asset body. Tables in the same batch that had already finished keep their own status.

"Orphaned: Dagster run \<id\> ... (In Progress for N min). Reset to retry." Written by
`dataloader_reconcile_monitor`. The run that owned the row is gone or finished and never reported.

### Finding the rest of a batch

Run tags do not carry `config_id` or `control_key`; a run owns many rows. The tags are
`db_config_key`, `table_count` and `tables` (source names, truncated to 200 characters). The
authoritative list is `tables` in the run config, under the op's `config`.

From the database side, go through the run URL:

```sql
SELECT source_table_name, last_status, last_status_message
FROM control.table_control
WHERE last_dagster_run = (
    SELECT last_dagster_run
    FROM control.table_control
    WHERE source_table_name = 'your_table'
)
ORDER BY last_status, source_table_name;
```

Both run URLs are cleared when a table is marked `Queued`, so a link never survives into the next
run.

---

## A table never runs

Symptom: the row looks healthy, the cron has passed, nothing happens, tick after tick.

Cause: the master sensor reads one view, `control.dataloader_control_vw`, defined in
`db/migrations/20260911030000_delete_mode.sql`. A row qualifies only when:

```sql
ctl.is_active = true
AND (
    ctl.last_status IS NULL
    OR (COALESCE(ctl.next_load_date_time::timestamptz,
                 CURRENT_TIMESTAMP - '1 mon'::interval) <= CURRENT_TIMESTAMP
        AND ctl.last_status = 'Succeeded')
)
```

A row sitting at `Failed` never runs again until something sets its status to NULL, however many
times its cron fires. The same is true of `Queued` and `In Progress`, which is what stops
double-triggering. Only `Succeeded` and NULL re-qualify, and a NULL skips the
due-time check entirely.

The other quiet exclusion is the INNER JOIN to `table_control_dbconfig`. A row whose
`db_config_key` is null or points at a deleted database configuration is invisible to the sensor
with no error anywhere.

Check:

```sql
SELECT is_active, last_status, next_load_date_time, load_cron, db_config_key
FROM control.table_control
WHERE source_table_name = 'your_table';

-- Does the sensor actually see it?
SELECT config_id, source_table_name, next_load_date_time
FROM control.dataloader_control_vw
WHERE source_table_name = 'your_table';
```

Fix: press Reset in the app, which sets `last_status` and `last_status_message` to NULL and closes
any open history row, or do the same in SQL:

```sql
UPDATE control.table_control
SET last_status = NULL,
    last_status_message = NULL,
    last_modified_at = CURRENT_TIMESTAMP
WHERE config_id = 123;
```

---

## Nothing is running at all

Symptom: no runs, no failures, everything quietly overdue.

Cause, in the order worth checking:

1. Sensors are off. Four of the eight ship stopped and have to be enabled in the Dagster UI after a
   fresh or restored deployment: `dataloader_master_sensor`, `dataloader_longqueued_monitor`,
   `dataloader_longrunning_monitor`, `dataloader_failed_monitor`. They are the four that matter
   most. The other four, `dagster_reload_sensor`, `dataloader_reconcile_monitor`,
   `dataloader_run_canceled` and `dataloader_run_failed`, ship running.
2. Nothing qualifies. Run the view query above. A fleet stuck at `Failed` produces exactly this
   symptom.
3. A backfill is gating a database. One `chunked_backfill` runs per source database at a time. The
   sensor tick log names every backfill it deferred and why.
4. The code location is down. The Dagster UI shows the location in error and the sensor cannot
   evaluate.

Those eight are the whole list. If you are looking for a sensor that generates staging models,
there isn't one: `get_new_landing_tables` and `mark_staging_model_processed` are still in
`lakebase_client.py`, but nothing calls them.

---

## Several tables from one database failed together

Symptom: five, ten or twelve failures appear in the same second, all with one `db_config_key`.

Cause: one batch died. A connection refused, a secret that no longer resolves, a cluster that went
away, a driver OOM. The pipe attributes the batch error to every table that had not already
finished, so the message repeats across rows.

Check:

```sql
SELECT last_dagster_run,
       db_config_key,
       COUNT(*) AS tables,
       MIN(last_status_message) AS message
FROM control.table_control
WHERE last_status = 'Failed'
  AND last_status_date_time > CURRENT_TIMESTAMP - INTERVAL '1 day'
GROUP BY 1, 2
HAVING COUNT(*) > 1
ORDER BY 3 DESC;
```

Fix: diagnose once, at the database or the cluster, then reset the whole group. Resetting one table
of a dead batch gets you one table's worth of the same failure a minute later.

---

## A table is stuck In Progress

Symptom: `In Progress` for hours, and the Databricks run has long since finished or vanished.

Cause: the run that owned the row died without reporting. Several layers cover this, and knowing
which one will act tells you how long to wait.

| Layer | Trigger | Wait |
|---|---|---|
| Pipe error handler | The batch raised after starting | Immediate |
| Asset fallback | The job submission itself raised | Immediate |
| `dataloader_run_canceled` / `dataloader_run_failed` | The run ended outside the asset body | Seconds |
| `dataloader_reconcile_monitor` | Everything else, including an unreadable run config | Up to 10 minutes after the row turns 20 minutes old |
| `dataloader_longrunning_monitor` | The run is genuinely still going past 180 minutes | Up to 10 minutes after the threshold |

The reconcile monitor is the backstop added after the September 2026 outage, when rows sat
In Progress for days because the runs that owned them had been killed by hand. Every 10 minutes it
takes rows In Progress for more than 20 minutes (active or not), looks up the recorded Dagster run,
and marks the row Failed when that run is missing or already finished. Rows whose run is still live
are left to the long-running monitor. A second pass closes `historical_metadata` rows that never
received a final status.

Check: follow `last_dagster_run` and `last_databricks_run`. If the Dagster run is still `STARTED`
and the Databricks run is finished, the Pipes completion signal was lost. The long-running monitor's
orphan phase terminates that run once it is 20 minutes old, before its 180 minute phase applies to
anything.

Fix: press Reset if you need it sooner than the monitors. If many rows are stuck at once, suspect a
stopped sensor rather than a bad table.

---

## A table is stuck Queued

Symptom: `Queued` and no run to show for it.

Cause: the sensor marked the batch `Queued` and the run never started, or started and died before
the asset wrote In Progress. Dagster run concurrency is `max_concurrent_runs: 64` on one VM, so a
backlog is normal for minutes, not hours.

`dataloader_longqueued_monitor` clears the wreckage: rows `Queued` longer than 60 minutes are
marked `Failed` with `Cancelled: queued for N minutes (threshold: 60 min)`. Despite the wording it
cancels nothing in Dagster. It only corrects the control row.

Check the Dagster run queue and the sensor's tick log before resetting. A row reset while its run
is still queued will be picked up twice.

---

## Connection timeout or authentication failure

Symptom: "Connection timeout", "Read timed out", "Login failed", or a secret that will not resolve.
Usually the whole batch.

Check the source first, not the config. Use Test connection on the database page. It connects on the
dataloader cluster with exactly the secrets configured and reports which ones resolved. It is
disabled on PROD.

If the source is healthy and one table times out while its siblings load, the read is too slow
rather than unreachable. Give it a partition column so the window comes over parallel JDBC
connections instead of one:

```sql
UPDATE control.table_control
SET partition_column = 'id',
    num_partitions = 8,
    lower_bound = '1',
    upper_bound = '1000000'
WHERE config_id = 123;
```

Use Suggest columns rather than guessing. It grades every column as a partition column and as an
incremental cursor from the source's own indexes.

---

## Out of memory

Symptom: `OutOfMemoryError`, "Java heap space", or an executor lost during the write.

Causes, in order of likelihood:

1. No partition column. The whole window arrives over a single JDBC connection into a single Spark
   partition. This is the usual one.
2. A first load of a very large table. Use `chunked_backfill`, not `load_full`.
3. Skew. A partition column whose range is unevenly populated puts most rows in one task.
4. Too much parallelism rather than too little. A wide table with a large fetch size can exhaust the
   driver. The Oracle driver preallocates prefetch buffers per row from the maximum column widths,
   which is why its fetch size is 20,000 against 100,000 elsewhere.

---

## Merge fails on the primary key

Symptom: "Duplicate key", or a MERGE that cannot match a single target row.

Cause: `primary_key_cols` is not unique in the source window. Either the key is genuinely composite
and only part of it is configured, or the source has duplicates.

Check against the source, in the window the loader actually reads:

```sql
SELECT <primary_key_cols>, COUNT(*)
FROM <schema>.<table>
WHERE <incremental_column> > '<incremental_value>'
GROUP BY <primary_key_cols>
HAVING COUNT(*) > 1;
```

Fix: add the missing key columns, or move the table to `append_only` if the duplicates are the
point. Delete reconciliation needs `primary_key_cols` too. Without it the loader skips the delete
check and logs that it did.

---

## Schema mismatch

Symptom: "Schema mismatch", or a column the destination does not have.

Most schema changes need no action. The loader sets
`spark.databricks.delta.schema.autoMerge.enabled`, appends with `mergeSchema`, and overwrites with
`overwriteSchema`. A new source column arrives on the next run and is filled for the rows in that
window.

What evolution cannot do is change a column's type or follow a rename. Both need a full reload:

```sql
UPDATE control.table_control
SET load_full = true          -- boolean since migration 20260908230000
WHERE config_id = 123;
```

`load_full` is a real boolean now. `SET load_full = 'true'` is rejected. The loader clears the flag
after a successful load, and the forced reload also bypasses the strategy's window, so an
incremental table genuinely reads everything rather than overwriting itself with one window.

For a very large table, use `chunked_backfill` instead of `load_full`.

---

## The cursor moves but rows are missing

Symptom: the load succeeds every time, `incremental_value` advances, and rows that were edited in
the source never appear in the destination.

Cause: the incremental column is a business date, not a change column. A well's spud date, an
invoice's due date, an effective date. It was set when the row was created and does not move when
somebody edits the row, so the edit sits below the cursor forever and is never read again.

This fails silently. Nothing errors, nothing is logged, the counts just run low.

Check with Suggest columns on the table. It grades each candidate and names this case directly: a
business date scores below an unnamed column so it is never picked by default.

| Grade | Meaning |
|---|---|
| rowversion | The engine's own change counter, loaded as bigint. The best option on SQL Server |
| looks like a modified timestamp | `updated`, `modified`, `last_chg`, `revised` and relatives |
| created-only name | Catches inserts, not updates |
| business date | Moves with the record, not with edits. Wrong for this job |

Fix: change `incremental_column` to a real change column, then force one full reload so the rows
missed while the wrong cursor was in use are recovered. Changing the cursor alone does not go back
for them.

A cursor that has simply stopped is a different problem with the same query:

```sql
SELECT source_table_name, incremental_column, incremental_value, last_load_date_time
FROM control.table_control
WHERE is_active = true
  AND load_strategy IN ('incremental', 'append_only')
  AND incremental_type = 'timestamp'
  AND incremental_value ~ '^\d{4}-\d{2}-\d{2}'
  AND incremental_value::timestamp < CURRENT_TIMESTAMP - INTERVAL '7 days'
ORDER BY incremental_value;
```

A cursor that has not moved in a week is either a table with no changes or a table whose window
returns nothing because the column is wrong.

---

## append_only duplicated rows

Symptom: rows appear twice in a destination loaded by `append_only`.

Cause: `append_only` reads strictly above its cursor and gets no lookback, by design. The loader
logs `[No lookback for append_only]: re-read rows would be appended twice`. An append cannot absorb
a re-read row the way a merge can, so a table that re-reads anything duplicates it.

The consequence runs the other way too. `incremental` re-reads a window below the cursor to catch
late commits, 12 hours by default (`DATALOADER_INCREMENTAL_LOOKBACK_HOURS`, overridden per table by
`incremental_lookback_hours`). `append_only` has no such safety net, so a row committed by a long
transaction after the cursor passed its timestamp is lost.

Fix: a table that needs both no duplicates and no losses needs a primary key and the `incremental`
strategy. If duplicates already exist, dedupe the destination and reload.

---

## An interrupted chunked backfill

Symptom: a backfill run was canceled, timed out at 24 hours, or died, and the destination holds
some of the table.

How resume works. Chunks cover `[start, end)` and every completed chunk records its upper bound.
Where chunks are cut on a numeric partition column, that mark goes in `backfill_cursor`. Where they
are cut on the incremental column, it goes in `incremental_value`. The next run reads the mark,
deletes destination rows at or above it (the leftovers of a chunk that appended without recording
itself), and continues. The first chunk of a fresh start overwrites the destination; every later
chunk appends.

```sql
SELECT source_table_name, load_strategy, incremental_value, backfill_cursor, partition_column
FROM control.table_control
WHERE config_id = 123;
```

Two things to know while it is running:

- The destination is partial. Chunk one replaced the whole table and the rest are still landing, so
  anyone querying it sees only the chunks loaded so far. If that is unacceptable, load into a new
  destination name and rename afterwards.
- Do not clear the mark to start clean. Clearing `backfill_cursor` or `incremental_value` makes the
  next run start from the source MIN and overwrite, throwing away hours of work. Clearing it is only
  correct when you also intend to rebuild the destination from scratch.

When the last chunk lands, the loader switches the row to `incremental`, sets the cursor, clears the
backfill mark, and writes a `table_control_history` row with `change_source = 'backfill_complete'`.
A row still on `chunked_backfill` days later never finished.

---

## A dev load is missing most of its rows

Symptom: a table loaded into a dev catalog holds exactly 1000 rows, and the run says Succeeded.

Cause: the dev row limit. Any load whose `destination_catalog` name contains `dev`, case
insensitive, is capped at 1000 rows using the source's own syntax (`TOP`, `ROWNUM`, `LIMIT`).
`dev_full_load = true` on the control row turns the cap off for that table. The loader logs
`[Applying dev row limit: 1000]` and nothing else complains, which is what makes it easy to miss.

The substring match is the trap. A catalog called `bronze_devices` is treated as a dev catalog.

```sql
SELECT source_table_name, destination_catalog, dev_full_load
FROM control.table_control
WHERE destination_catalog ILIKE '%dev%'
  AND dev_full_load = false;
```

The cap is applied where the loader builds a query: full, incremental, append_only, rolling and
Iceberg reads. It is not applied to the partitioned first-load and forced-reload path, to
`check_and_load`'s whole-table re-read, or to `chunked_backfill` chunks, so a table can be capped on
one run and complete on the next.

---

## Names with spaces or punctuation

Two separate problems with one root.

Source side. A table called `Well Header Sample` reached the JDBC reader unquoted and SQL Server
stopped parsing at `Upload`. The loader now quotes any identifier its engine cannot take bare:
square brackets for SQL Server, backticks for MySQL and ClickHouse, double quotes elsewhere. Plain
identifiers are deliberately left unquoted, because quoting makes Oracle and PostgreSQL names
case-sensitive and every existing config relies on the engine's own folding. If a source name fails
to resolve, check its case against the source catalog before adding quotes by hand.

Destination side. Unity Catalog takes letters, digits and `_`. Delta rejects the rest at the
first write, after the source read has already happened. Migration
`20260916010000_safe_destination_names` folded the existing offenders, and dl-app now rejects such
names on save and suggests a folded version. Rows created by an older app version or by direct SQL
can still carry one:

```sql
SELECT config_id, source_table_name, destination_table_name
FROM control.table_control
WHERE destination_table_name ~ '[^A-Za-z0-9_]';
```

---

## AI retry is not retrying

Symptom: a table sits at `Failed` and `dataloader_failed_monitor` never picks it up.

Two causes.

The message carries the review marker. `mark_for_review` prepends `[AI Analysis - Requires Review]`
to `last_status_message` and leaves the status at `Failed`. The monitor's own query excludes those
rows so it does not re-analyze them. Resetting the table clears the message and puts it back in
scope.

The retry budget is lifetime, not per incident. There is no `retry_count` column. The monitor
derives the count as the number of `historical_metadata` rows for that `control_key` with
`load_status = 'Failed'`, with no time window at all. A table with three failed executions anywhere
in its history is permanently out of automatic retries:

```sql
SELECT tc.source_table_name,
       COUNT(*) FILTER (WHERE hm.load_status = 'Failed') AS lifetime_failures
FROM control.table_control tc
JOIN control.historical_metadata hm ON hm.control_key = tc.control_key
WHERE tc.last_status = 'Failed'
GROUP BY 1
ORDER BY 2 DESC;
```

There is nothing to set to zero. Reset the table by hand and fix the underlying cause. Automatic
retry is a convenience for transient failures, not a repair mechanism.

When it does work, the monitor runs every 15 minutes, sends the error to an LLM, and either calls
`reset_for_retry` (status NULL, `next_load_date_time` now, `[AI Analysis - Retry Recommended]`
prepended to the message) or `mark_for_review`.

---

## Useful queries

### Stuck In Progress, with the run to open

```sql
SELECT config_id,
       source_table_name,
       db_config_key,
       last_dagster_run,
       last_databricks_run,
       ROUND(EXTRACT(EPOCH FROM (
           CURRENT_TIMESTAMP - COALESCE(last_load_date_time, last_status_date_time)
       ))/60) AS minutes_in_progress
FROM control.table_control
WHERE last_status = 'In Progress'
ORDER BY minutes_in_progress DESC;
```

### Failure counts by database, last 7 days

```sql
SELECT tc.db_config_key,
       COUNT(*) FILTER (WHERE hm.load_status = 'Failed')    AS failed,
       COUNT(*) FILTER (WHERE hm.load_status = 'Succeeded') AS succeeded,
       COUNT(DISTINCT tc.config_id) FILTER (WHERE hm.load_status = 'Failed') AS distinct_tables
FROM control.historical_metadata hm
JOIN control.table_control tc ON tc.control_key = hm.control_key
WHERE hm.load_start_time > CURRENT_TIMESTAMP - INTERVAL '7 days'
GROUP BY 1
ORDER BY failed DESC;
```

A high `failed` with a low `distinct_tables` is one table failing repeatedly. The reverse is a
database or a batch problem.

### Cursors that have not moved in N days

```sql
SELECT source_table_name,
       load_strategy,
       incremental_column,
       incremental_value,
       last_load_date_time
FROM control.table_control
WHERE is_active = true
  AND load_strategy IN ('incremental', 'append_only')
  AND incremental_type = 'timestamp'
  AND incremental_value ~ '^\d{4}-\d{2}-\d{2}'
  AND incremental_value::timestamp < CURRENT_TIMESTAMP - INTERVAL '14 days'
ORDER BY incremental_value;
```

`incremental_value` is a VARCHAR, so the regex guard keeps the cast away from values that are not
timestamps. Change the interval to taste.

### Open history rows

Executions that started and never finished. The reconcile monitor closes these, so a long list means
it is stopped or something is outrunning it.

```sql
SELECT hm.id,
       hm.source_schema,
       hm.source_table,
       tc.last_status,
       tc.last_dagster_run,
       ROUND(EXTRACT(EPOCH FROM (
           CURRENT_TIMESTAMP - COALESCE(hm.load_start_time, hm.load_queued_time)
       ))/60) AS minutes_open
FROM control.historical_metadata hm
LEFT JOIN control.table_control tc ON tc.control_key = hm.control_key
WHERE hm.load_status IS NULL
  AND COALESCE(hm.load_start_time, hm.load_queued_time)
      < CURRENT_TIMESTAMP - INTERVAL '20 minutes'
ORDER BY hm.id;
```

### One table's recent executions

```sql
SELECT hm.load_status,
       hm.load_strategy,
       hm.rows_processed,
       hm.total_duration,
       hm.load_start_time,
       hm.log_message
FROM control.historical_metadata hm
JOIN control.table_control tc ON tc.control_key = hm.control_key
WHERE tc.source_table_name = 'your_table'
ORDER BY hm.load_start_time DESC
LIMIT 10;
```

### Reset every failed table for one database

```sql
UPDATE control.table_control
SET last_status = NULL,
    last_status_message = NULL,
    last_modified_at = CURRENT_TIMESTAMP
WHERE last_status = 'Failed'
  AND db_config_key = 'your_db_config';
```

Fix the cause first. These rows will be back within the hour otherwise.

### Who changed what

```sql
SELECT table_name, change_type, change_source, changed_by, changed_at, changed_fields
FROM control.table_control_history_vw
WHERE table_name = 'your_table'
ORDER BY changed_at DESC
LIMIT 10;
```

`change_source = 'backfill_complete'` is written by the loader, not by a person.

---

## Related documentation

- [Dagster Orchestration](03-dagster-orchestration.md), batching, the eight sensors, recovery layers
- [Lakebase Control Database](02-lakebase-control-database.md), schema and the view predicate
- [Load Strategies](05-load-strategies.md), cursors, lookback, deletes, the dev row limit
- [Common Tasks](08-common-tasks.md), the routine version of most of the fixes above
- [Control Manager UI](07-control-manager-ui.md), where Operations, Runs and Reset live
