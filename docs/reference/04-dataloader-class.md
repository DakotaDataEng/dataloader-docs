# DataLoader Class

`DataLoader` is what runs on Databricks. One instance binds to one source database, then loads
up to twelve tables on twelve threads. Everything it needs that does not require Spark lives in
sibling modules under `dbx/functions/`, so it can be unit tested off a cluster.

---

## Overview

| Property | Value |
|---|---|
| Location | `dbx/functions/dataloader.py`, 5,237 lines |
| Runtime | Python on Databricks, PySpark and Delta |
| Threading | `ThreadPoolExecutor`, 12 workers by default |
| Source types | 7 |
| Load strategies | 6 (see [Load Strategies](05-load-strategies.md)) |
| Supporting modules | 14 under `dbx/functions/`, 2,622 lines |

---

## Module inventory

The loader is one big file, but it is not the whole system. Anything that can be written without
Spark was moved out, because a module that imports `pyspark` and `delta` cannot be imported on a
laptop and so cannot be tested there.

| Module | Responsibility |
|---|---|
| `dataloader.py` | The `DataLoader` class: connections, source reads, Delta writes, deletes, backfills, results |
| `batch_results.py` | Turns a run config spec into the dict the loader expects, and attributes a batch's results back to tables by `control_key` |
| `connections.py` | Login and socket timeouts, application name and session init statement for every source connection |
| `identifiers.py` | Quotes source identifiers per dialect, and folds destination names to what Delta accepts |
| `incremental.py` | Cursor rules for the window strategies: lookback, cursor capping, fetch size, when a window is worth reading in parallel, Delta tuning statements |
| `partitioning.py` | Partitioned JDBC read maths: bound formatting and normalization, partition and chunk counts, chunk parallelism |
| `backfill.py` | Resume planning for chunked backfills: high-water marks, which column the chunks are cut on, hand-over to the incremental strategy |
| `delete_modes.py` | Resolves `delete_mode` to `soft` or `hard`, falling back to `soft` for anything unrecognised |
| `catalog_crawl.py` | Per-engine catalog queries and a pure fold into one record per source table, for `control.source_catalog` |
| `column_advice.py` | Ranks source columns as incremental and partition candidates from index and type metadata |
| `preview.py` | First-N-rows query per engine, and cell rendering safe to show in a browser |
| `source_secrets.py` | Resolves a dbconfig row's Key Vault names into values the way the pipe does, for the one-off notebooks |
| `event_logger.py` | `EventLogger`: writes the run's event JSON to Azure Blob Storage, service principal or account key |
| `dagster.py` | Reports an asset materialization to Dagster over REST, with retries |
| `sql_server_connection_test.py` | Standalone pyodbc and socket diagnostic script for a SQL Server source, not part of a load |

---

## Constructor

```python
DataLoader(
    db_type,                      # source type
    database_config,              # resolved credentials, not Key Vault names
    full_load="N",                # str or bool
    control_schema="control",
    load_strategy=None,           # filter tables by strategy
    use_threading=True,
    max_workers=12,
    lakebase_config=None,
    dev_row_limit=1000,
    chunk_jdbc_partitions=None,
)
```

| Parameter | Default | Meaning |
|---|---|---|
| `db_type` | required | `mssql`, `oracle`, `postgresql`, `clickhouse`, `snowflake`, `snowflake_pem`, `s3_iceberg` |
| `database_config` | required | Connection values, already resolved from Key Vault |
| `full_load` | `"N"` | Accepts a bool or a string. `True`, `"true"`, `"Y"`, `"yes"`, `"1"` turn it on, anything else is off. The pipe passes a real bool from the run config's `load_full` |
| `control_schema` | `"control"` | Lakebase schema. The database name, `dataloader` or `dataloader_test`, is what decides the environment |
| `load_strategy` | `None` | Process only tables of this strategy |
| `use_threading` | `True` | Set `False` to run tables one at a time when debugging |
| `max_workers` | `12` | Thread pool size, matched to `TABLES_PER_RUN` in the sensor |
| `lakebase_config` | `None` | Falls back to the `LAKEBASE_*` environment variables |
| `dev_row_limit` | `1000` | Rows read per table when the destination catalog is a dev catalog |
| `chunk_jdbc_partitions` | `None` | Parallel JDBC connections per backfill chunk. Resolved from the cluster when not given |

The `load_strategy` filter validates against `full`, `incremental`, `append_only`, `rolling` and
`chunked_backfill`. `check_and_load` is missing from that list, so passing it as a filter raises
`ValueError` even though the strategy itself is supported everywhere else in the class.

---

## Source databases

Five engines go through JDBC. Snowflake uses its own Spark connector, and `s3_iceberg` reads
files with PyIceberg and boto3, no Spark JARs and no SQL.

| Type | URL or connector | Driver |
|---|---|---|
| `mssql` | `jdbc:sqlserver://{host}:{port};databaseName={service_name};trustServerCertificate=true` | `com.microsoft.sqlserver.jdbc.SQLServerDriver` |
| `oracle` | `jdbc:oracle:thin:@{host}:{port}/{service_name}` | `oracle.jdbc.driver.OracleDriver` |
| `postgresql` | `jdbc:postgresql://{host}:{port}/{service_name}` | `org.postgresql.Driver` |
| `clickhouse` | `jdbc:clickhouse://{host}:{port}/{service_name}` | `com.clickhouse.jdbc.ClickHouseDriver` |
| `snowflake` | Native connector, password auth | none |
| `snowflake_pem` | `net.snowflake.spark.snowflake`, key pair auth | none |
| `s3_iceberg` | PyIceberg `StaticTable` over S3 | none |

Every engine takes `service_name`, not `database`. It is the same control column whatever the
engine calls it: a SQL Server database, an Oracle service, a PostgreSQL database, a Snowflake
database. The settings from `connections.py` are layered on the URL or the driver properties
after the template is filled in.

### database_config shapes

**mssql**

```python
{
    "host": "server.database.windows.net",
    "port": "1433",
    "service_name": "SalesDB",
    "user": "dataloader",        # client_id when tenant_id is present
    "password": "secret",        # client_secret when tenant_id is present
    "tenant_id": "...",          # optional, presence switches to service principal auth
}
```

**oracle**

```python
{"host": "...", "port": "1521", "service_name": "ORCL", "user": "...", "password": "..."}
```

**postgresql**

```python
{"host": "...", "port": "5432", "service_name": "analytics", "user": "...", "password": "..."}
```

**clickhouse**

```python
{"host": "...", "port": "8123", "service_name": "default", "user": "...", "password": "..."}
```

**snowflake**

```python
{"url": "...snowflakecomputing.com", "user": "...", "password": "...",
 "service_name": "RAW", "warehouse": "COMPUTE_WH"}
```

**snowflake_pem** drops `password` and adds `pem_private_key`.

**s3_iceberg**

```python
{"aws_access_key_id": "...", "aws_secret_access_key": "...",
 "aws_region": "us-east-1", "s3_warehouse": "s3a://bucket/prefix"}
```

### Azure SQL service principal auth

A `tenant_id` in an mssql config switches the connection from user and password to an Entra
token. `_acquire_azure_sql_token` builds a `ClientSecretCredential` from `azure-identity` and
asks it for `https://database.windows.net/.default`. The token goes into the connection options
as `accessToken` instead of `user` and `password`.

```python
access_token = self._acquire_azure_sql_token(
    database_config["tenant_id"],
    database_config["user"],      # client_id
    database_config["password"],  # client_secret
)
self.connection_string = {
    "url": with_suffix(conn_template.format(**database_config), self.db_type),
    "accessToken": access_token,
    "driver": drivers[self.db_type],
}
```

Debug logging goes through `_redact_connection_string`, which masks `password`, `sfPassword`,
`pem_private_key`, `accessToken` and `aws_secret_access_key`.

---

## connections.py

Four settings, all environment driven so a source can be tuned without a release.

| Setting | Env var | Default | Applied as |
|---|---|---|---|
| Login timeout | `DATALOADER_LOGIN_TIMEOUT_SECONDS` | 30s | `loginTimeout` in the mssql and postgresql URLs, `oracle.net.CONNECT_TIMEOUT` in ms for Oracle |
| Socket timeout | `DATALOADER_SOCKET_TIMEOUT_SECONDS` | 3600s | `socketTimeout`, ms for mssql and seconds for postgresql, `oracle.jdbc.ReadTimeout` in ms for Oracle |
| Application name | fixed, `dataloader` | | `applicationName` for mssql, `ApplicationName` for postgresql, `v$session.program` for Oracle |
| Session init statement | `DATALOADER_SESSION_INIT_<DBTYPE>` | unset | `sessionInitStatement`, which Spark runs on every new connection |

`with_suffix` appends the URL parts and respects a URL that already carries a query string.
`driver_properties` covers Oracle, which takes its timeouts as properties rather than URL parts.
Snowflake and `s3_iceberg` get nothing here.

Why it exists: a connection with no timeout holds a worker thread until the Databricks job
timeout when the socket hangs. Three hours of a twelve-table run spent on a dead socket, and the
other eleven tables share what is left. The socket timeout is deliberately generous at an hour,
because a chunk query on an unindexed column can run for many minutes before the first row
arrives. The application name is the other half. An unnamed connection is anonymous in the
source's session views, so the source's DBAs cannot tell the loader apart from anything else
holding a long read.

A typical use of the session init statement is
`DATALOADER_SESSION_INIT_MSSQL='SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED'`, for a source
whose writers must never wait on the loader.

---

## identifiers.py

A source table called `Well Header Sample` reached the JDBC reader as
`dbo.Well Header Sample`, and SQL Server parsed it as far as `Header`. Quoting fixes that,
but quoting everything breaks more than it fixes: quoted names are case-sensitive in Oracle and
PostgreSQL, and existing configs rely on the engine folding case for them. So the rule is
conservative. A name matching `^[A-Za-z_][A-Za-z0-9_$#@]*$` is passed through exactly as it is.
Only a name the engine cannot parse bare gets quoted.

| Dialect | Quotes | Escape |
|---|---|---|
| `mssql` | `[name]` | `]` doubled |
| `mysql`, `clickhouse` | backticks | backtick doubled |
| `postgresql`, `oracle`, `snowflake`, SQL standard | `"name"` | `"` doubled |

`qualified_source(schema, table, db_type)` quotes each part separately, and is what
`_get_base_query` returns when the control row has no custom query.

Destination names are a separate rule. Unity Catalog and Delta take letters, digits and
`_` in a table name and nothing else, so `safe_destination_name` folds every run of anything
else into a single `_`: `Well Header Sample` becomes `Well_Header_Sample`. dl-app calls
`is_safe_destination_name` when a config is saved and refuses a bad name with the folded version
as the suggestion, which is cheaper than failing at the first write.

---

## Reading the source

`load_table_from_source` picks the read path, `build_query` builds the SQL, and the `_read_*`
helpers run it.

| Path | When |
|---|---|
| `_load_iceberg_table` | `db_type` is `s3_iceberg` |
| `_read_partitioned` | A forced full reload with a partition column, or the first load of an `append_only`, `incremental` or `rolling` table |
| `_read_window` | The normal incremental, append_only or rolling window |
| `_read_single` | Everything else, and any window not worth splitting |

### Windowed reads

`build_query` filters on the cursor. The dialect literal differs per engine.

| Engine | Timestamp filter |
|---|---|
| `postgresql` | `col > ('<value>'::timestamp + INTERVAL '1 second')` |
| `mssql` | `col > CAST('<value>' AS DATETIME2)` |
| `oracle` | `col > TO_TIMESTAMP('<value>', 'YYYY-MM-DD HH24:MI:SS')` |
| `clickhouse` | `col > toDateTime('<value>')` |
| `snowflake` | `col > TO_TIMESTAMP('<value>')` |

Oracle compares the column itself rather than a `CAST` of it. The cast defeated any index on the
column, so every run was a full scan, and it dropped sub-second precision.

**Lookback.** An incremental load reads from the cursor minus
`DATALOADER_INCREMENTAL_LOOKBACK_HOURS` (12 by default), or the row's own
`incremental_lookback_hours`. Rows re-read inside that window are absorbed by the merge.
`append_only` gets no lookback at all, because an append would duplicate them.

**Parallel windows.** A window used to arrive over one connection into one Spark partition
however big it was, which is slow and the usual way a worker ran out of memory. `_read_window`
takes MIN and MAX of the partition column over the window in one round trip and, when the row has
a partition column and a count and the window is wide enough to be worth it, reads it
partitioned. An empty or single-valued window stays on one connection.

**Bounds.** `_read_partitioned` reads the partition column's type from the query schema and
reshapes the stored bounds to match, because Spark parses date bounds as `yyyy-MM-dd` and
timestamp bounds as `yyyy-MM-dd HH:mm:ss` and rejects the other form. A row with no bounds gets
them from one MIN/MAX of the key, and they are written back to the control row so dl-app shows
what the loader used.

### Dev row limit

When the destination catalog name contains `dev` and the row does not set `dev_full_load`, every
query is wrapped in the engine's own first-N form for `dev_row_limit` rows, 1,000 by default:
`TOP` for SQL Server, `ROWNUM` for Oracle, `LIMIT` elsewhere. Iceberg reads take the same limit
by stopping the Arrow scan early. `dev_full_load` on a row turns it off for that table, for when
the dev copy needs to be complete.

### Iceberg

`s3_iceberg` has no source-side SQL, so `check_and_load` and `chunked_backfill` raise
`ValueError`. The rest of the path is the same shape. boto3 lists `metadata/` under the warehouse
prefix and takes the newest `.metadata.json`, PyIceberg opens it as a `StaticTable`, and the
strategy's filter becomes an Iceberg `GreaterThan` or `GreaterThanOrEqual` expression pushed into
the scan. The Arrow result is converted to a Spark DataFrame and joins the normal Delta write
path. The schema comes from the Iceberg metadata rather than from the data, so a column that
happens to be all nulls keeps its type.

### Geometry and types

| Engine | Detection | Conversion |
|---|---|---|
| `mssql` | `INFORMATION_SCHEMA` types `geometry` and `geography` | `col.AsBinaryZM() as col` plus `col.STSrid as col_srid` |
| `oracle` | ST_GEOMETRY columns | `SDE.ST_AsBinary(col) AS col_wkb` plus `sde.ST_SRID(col) AS col_srid` |
| `postgresql` | `udt_name` of `geometry` or `geography` | `ST_AsEWKB(col) AS col` plus `ST_SRID(col) AS col_srid` |

Binary, not text. WKB and EWKB keep Z and M coordinates, and the SRID rides along in its own
column. If the column list cannot be read the builder falls back to `SELECT *` with a warning
rather than failing the load.

SQL Server also casts a `rowversion` cursor column to `BIGINT`, so the destination gets something
it can take a `MAX` of instead of `binary(8)`.

ClickHouse types are inferred from `system.columns` rather than trusted from the driver, which
reports string types as `CHAR(0)` and `VARCHAR(0)` and breaks the Delta write.
`parse_clickhouse_type` unwraps `Nullable()` and `LowCardinality()`, then picks a cast:
`toString` for string, UUID, enum and very large integer types, `IPv4NumToString` and
`IPv6NumToString` for addresses, `toJSONString` for `Array`, `Map`, `Tuple` and `Nested`, and
`toDecimal64(col, 0)` for `UInt64`. The same map builds a Spark `customSchema` string, and
`cleanup_spark_char_types` catches anything that still arrives as CHAR or VARCHAR.

### Column names

`transform_column_names` runs on every DataFrame. Spaces and slashes become `_`, then
anything that is not alphanumeric or `_` is dropped. `Order ID` becomes `Order_ID`,
`Price/Unit` becomes `Price_Unit`, `Value ($)` becomes `Value_`.

---

## Writing to Unity Catalog

`write_table_to_unity` is what a worker thread runs. It checks the destination exists (creating
the schema once per run, under a lock), reads the row's tuning fields, loads the source, adds
`LastLoadDateTime` when `add_loadtime` is set, writes, then marks deletes.

The row count comes from the Delta commit, not from `df.count()`. Counting the frame before the
write ran the source query a second time.

| Scenario | Method |
|---|---|
| New table, or `full_load` | `overwrite_df_to_unity()` |
| `incremental` | `merge_incremental_table()` |
| `append_only` | `append_only_df_to_unity()` |
| `rolling` | `merge_rolling_table()` |
| `chunked_backfill` | `process_chunked_backfill()`, routed before the try block |
| Anything else on an existing table | `overwrite_df_to_unity()` |

An empty frame with no columns is `check_and_load` saying nothing changed. It writes nothing and
reports zero rows.

### Merges

Both merges stage first. The window is written to `<table>_stage` with overwrite, merged, then
the stage is dropped.

```sql
MERGE INTO target USING staging
  ON target.pk1 = staging.pk1 AND target.pk2 = staging.pk2
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
```

With soft deletes on, the update and insert clauses are explicit instead of `*`, so an inserted
row gets `is_delete = false` and `deleted_at = NULL`. A new row arrives live, never pre-deleted.

An empty stage short-circuits. Nothing is merged and the method returns zero.

The new cursor is `MAX` over the stage, not over the destination. It is cheaper, and a
future-dated row already sitting in the table cannot set it.

### Rolling

The rolling merge adds a third clause that trims the window:

```sql
WHEN NOT MATCHED BY SOURCE
  AND target.rolling_column >= (DATE('<today>') - INTERVAL '<rolling_days>' DAY)
THEN DELETE
```

The guard on the empty stage matters more here than anywhere else. An empty source window says
nothing about the destination, and running the not-matched-by-source delete against it would
empty the whole window.

### Delta tuning

`_ensure_table_tuning` runs once per table per run. It sets deletion vectors, `optimizeWrite` and
`autoCompact` as real table properties (the writer option used before was not one), and turns on
liquid clustering on the primary key for tables that merge on it, so a merge prunes files instead
of touching all of them. Existing data is not reclustered, `OPTIMIZE` does that when someone runs
it. `DATALOADER_TABLE_TUNING=off` disables the step.

Each thread also gets its own Spark scheduler pool, so with the cluster in FAIR mode twelve
tables share the executors instead of queueing behind the first big merge.

---

## Deletes and drift

`compare_source_keys` runs after the write when the row sets `is_delete`, `drift_check`, or both.
It reads every source primary key once and answers two questions from the same frames.

**Rows gone from the source**, when `is_delete` is on. Destination keys anti-joined against source
keys.

**Rows the destination never got**, when `drift_check` is on. The same join the other way round.
These are rows whose incremental column never moved above the cursor, so no window will ever read
them and no lookback reaches them. They are counted into `drift_missing_rows` and never loaded:
the repair is a full reload, which is a decision rather than something a health check makes.

The second answer is close to free on a table that already reconciles deletes, because reading the
source keys is the expensive half and both frames are already cached. `drift_check` on its own
pays for that read, which is why it is off by default and the edit form says which case a table
is in.

- `soft`, the default: set `is_delete = true` and `deleted_at = current_timestamp()`, keep the
  row. `deleted_at` is not overwritten on later runs, so it works as an effective date. A row
  that comes back in the source is restored.
- `hard`: delete the row from the destination. No bookkeeping columns, no restore.

`ensure_delete_columns` adds `is_delete BOOLEAN` and `deleted_at TIMESTAMP` on demand, so turning
delete tracking on does not mean rebuilding the table. It only runs for soft deletes.

Two guards. The strategy must be `incremental`, `append_only`, `rolling` or `check_and_load`, and
`primary_key_cols` must be set. If the source key query comes back empty while the destination
still holds rows, the whole reconciliation is skipped with a warning: a failed or filtered source
query would otherwise flag or delete the entire table. An unrecognised `delete_mode` resolves to
`soft` and logs that it did.

The pass does not run on every load. `DATALOADER_DELETE_CHECK_HOURS`, 24 by default, decides how
often, stamped on the control row as `deletes_checked_at`. Set it to 0 for every run. When only
`drift_check` is on, `DATALOADER_DRIFT_CHECK_HOURS` and `drift_checked_at` do the same job. When
both are on, deletes set the pace: the drift answer rides along with them, so there is nothing to
gain from reading the keys again in between.

A table with `drift_check` on and `is_delete` off is never flagged, so it is not asked for the
`is_delete` column it does not have.

The two settings are independent. One read of the source keys serves both, but each answer is only
computed when its own flag is set.

The source key read is `SELECT <primary keys> FROM <source schema>.<source table>`, not the table's
custom query. That makes the missing-row answer meaningless on a table loaded by a custom query,
since every filtered-out row would be reported missing, so that half is skipped there and logged.
Deletes are unaffected: the destination is a subset of the source in that case.

### Row counts

`count_rows_both_sides` runs after every windowed load and is the cheap tier. Delta answers
`COUNT(*)` from the transaction log and the source count comes from the engine's catalog through
`_source_row_estimate`, the same read that sizes backfill chunks. Neither side scans data.

Counts see net drift only. Ten rows inserted without the watermark moving and ten rows deleted
cancel out and the check stays quiet. That is why it is a tripwire for when to look, and the key
comparison is what proves anything.

Two details keep it honest:

- Soft-deleted rows are subtracted from the Delta count. Without that, every table using soft
  deletes would report permanent, growing drift. The flagged total comes from `drift_soft_deleted`,
  counted for free by the key comparison.
- The source number is not equally good on every engine. SQL Server, Snowflake and ClickHouse
  maintain a true count. Oracle's `num_rows` is as fresh as the last stats gather and PostgreSQL's
  `reltuples` as the last vacuum. `drift_source_exact` records which case applies and the UI marks
  the estimates.

Cheap is not free: two small Spark jobs, one JDBC round trip and one control row update on every
run of a windowed table, including the runs that load nothing. `DATALOADER_DRIFT_COUNTS=off` turns
it off.

---

## Chunked backfill

`process_chunked_backfill` walks a large table in sequential chunks, each appended, so a first
load does not have to fit in one read.

**Which column the chunks are cut on.** `choose_chunk_column` prefers the partition column when
it is set, distinct from the incremental column, and numeric on the source. Each chunk then
filters an indexed key range instead of scanning for a window of a usually unindexed timestamp.
Anything else falls back to the incremental column.

**Chunk count** is always calculated, never taken from `num_partitions`. Key mode aims at
`DATALOADER_ROWS_PER_CHUNK` rows per chunk from the catalog's row estimate, or from the key range
as a dense proxy. Timestamp mode cuts about 30 days per chunk, raised when the row estimate says
equal time slices would put too much of the table in its recent chunks.

**Within a chunk**, reads go parallel over `chunk_jdbc_partitions` JDBC connections when the row
has a partition column. The default is two per cluster core, clamped to between 8 and 64, and
`DATALOADER_CHUNK_JDBC_PARTITIONS` overrides it. The old fixed default of 4 left a large cluster
idle. It does not apply to Snowflake, which is not JDBC.

**Resume.** Every completed chunk writes its upper bound to the control row straight away, so a
canceled or failed backfill leaves a high-water mark. Key-mode backfills keep theirs in
`backfill_cursor` so `incremental_value` can stay a timestamp for the incremental strategy that
takes over. Timestamp-mode backfills use `incremental_value` itself. `plan_resume` then decides:
continue and append when the destination exists and a mark is set, start over and overwrite when
the destination is gone, do nothing when the mark is already at or past the source MAX. Rows at
or above the mark are deleted first, since they can only come from a chunk that appended but
never recorded itself.

The control row read at the start is strict. A Lakebase error used to read as "no high-water
mark", and the first chunk then overwrote everything an earlier run had loaded.

---

## Threading model

```python
with ThreadPoolExecutor(self.max_workers) as executor:
    futures = {
        executor.submit(self.write_table_to_unity, table): table
        for table in filtered_table_list
    }
    for future in as_completed(futures):
        error = future.exception()
        if error is not None:
            ...  # log the table and the exception type
```

`submit` and `as_completed`, not `map`. `map`'s results were never consumed, so an exception that
escaped a worker (a chunked backfill re-raises) vanished with no trace anywhere.

Each thread owns one table from source read to Delta write. Setting `use_threading=False` runs
them one at a time, which is how to isolate a single table's problem.

**Locks.** Two module-level locks, both narrow.

| Lock | Protects |
|---|---|
| `_POOL_LOCK` | Building the Lakebase connection pool once, double-checked, so twelve threads starting together do not each build one |
| `_SCHEMA_LOCK` | The set of schemas already created this run, so `CREATE SCHEMA IF NOT EXISTS` runs once per schema instead of once per table |

The result lists are still appended to without a lock, which CPython's `list.append` makes safe.

---

## Results

`get_results()` returns the three tracking lists.

```python
update_list, errors_list, completed_list = loader.get_results()
```

| List | Contents |
|---|---|
| `update_list` | Cursors staged for the batch control table update |
| `errors_list` | One record per failed table |
| `completed_list` | One record per loaded table, with row count and timing |

The pipe polls these lists while `process_tables` is still running and records each table's
outcome as soon as it appears, so a slow table does not hold the rest at `In Progress`.

### Record shape

```python
{
    "control_key": table_dict.get("control_key"),
    "table_name": "catalog.schema.table",
    "source_table": "schema.table",
    "load_strategy": "incremental",
    "error_message": str(e),
    "error_type": type(e).__name__,
    "error_traceback": traceback.format_exc(),
    "timestamp": "2026-09-16 10:15:30 -06:00",
    "status": "failed",
}
```

Results are matched to control rows by `control_key`, never by table name, because cloned configs
can share a destination name. Without it a record cannot be attributed to a table at all, so
`index_results_by_control_key` drops any record that has none. That is deliberate: the control
table update error record describes the batch, not any one table.

Success records carry the same identity fields plus `rows_processed`, `duration_seconds`,
`duration_human`, `start_time` and `end_time`. A backfill's record also carries `total_chunks`.

### Cursors

`set_last_update_value` records the new cursor twice. It writes it to the table's own control row
straight away, so a batch that stops early keeps every finished table's progress, and it stages
the same value in `update_list` for the one batch `UPDATE` at the end of `process_tables`.

The cursor is capped at the run start in the source's clock plus
`DATALOADER_CURSOR_CAP_MARGIN_HOURS`, one hour. A single row stamped in 2087 would otherwise
become the high-water mark and silently skip everything committed between now and then.

---

## log_event_data()

Returns the run's whole event payload, which the pipe writes to blob storage through
`EventLogger` and the hourly Dagster job merges into `admin.ingestion_logs`.

| Section | Holds |
|---|---|
| `execution` | `execution_id`, Dagster run id and URL, status, failure type and summary, environment, control schema, notebook path |
| `timing` | Start, end, `duration_seconds`, `duration_human` |
| `configuration` | `source_type`, `load_strategy_filter`, `full_load_override`, `threading_enabled`, `thread_count` |
| `results` | `tables_attempted`, `tables_succeeded`, `tables_failed`, `total_rows_processed`, `success_rate` |
| `tables` | `succeeded` and `failed`, the two record lists in full |
| `logs` | Every `log()` entry with timestamp and severity |
| `environment` | Cluster id, workspace URL, Spark and DBR versions, Databricks run URL |
| `metadata` | `event_type`, `schema_version`, `generated_at` |

Status is derived: `succeeded`, `failed`, `partial_failure` when some tables loaded and some did
not, and `error` when nothing was attempted at all.

---

## Environment variables

| Variable | Default | Effect |
|---|---|---|
| `DATALOADER_LOGIN_TIMEOUT_SECONDS` | 30 | Connection attempt timeout |
| `DATALOADER_SOCKET_TIMEOUT_SECONDS` | 3600 | Read timeout on an open connection |
| `DATALOADER_SESSION_INIT_<DBTYPE>` | unset | Statement run on every new connection to that engine |
| `DATALOADER_INCREMENTAL_LOOKBACK_HOURS` | 12 | Default lookback for incremental loads |
| `DATALOADER_CURSOR_CAP_MARGIN_HOURS` | 1 | How far past the run start a cursor may go |
| `DATALOADER_DELETE_CHECK_HOURS` | 24 | How often the key comparison reconciles deletes, 0 means every run |
| `DATALOADER_DRIFT_CHECK_HOURS` | 24 | How often the key comparison runs for drift alone, 0 means every run |
| `DATALOADER_DRIFT_TOLERANCE_PCT` | 1 | How far the two row counts may be apart before the table is called out |
| `DATALOADER_DRIFT_COUNTS` | `on` | `off` skips the row count step |
| `DATALOADER_ROWS_PER_CHUNK` | | Target rows per backfill chunk in key mode |
| `DATALOADER_BACKFILL_CURSOR_MARGIN_HOURS` | | Margin on the cursor handed over at the end of a backfill |
| `DATALOADER_CHUNK_JDBC_PARTITIONS` | two per core, 8 to 64 | JDBC connections per backfill chunk |
| `DATALOADER_TABLE_TUNING` | `on` | `off` skips the Delta tuning step |
| `LAKEBASE_HOST`, `LAKEBASE_PORT`, `LAKEBASE_DATABASE`, `LAKEBASE_USER`, `LAKEBASE_PASSWORD` | | Control database, when `lakebase_config` is not passed |

---

## Related documentation

- [System Overview](01-system-overview.md)
- [Lakebase Control Database](02-lakebase-control-database.md)
- [Dagster Orchestration](03-dagster-orchestration.md)
- [Load Strategies](05-load-strategies.md)
- [Troubleshooting](06-troubleshooting.md)
- [Sequence Diagrams](../diagrams/03-sequence-diagrams.md)
