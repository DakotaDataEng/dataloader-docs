# Troubleshooting Guide

Common issues and debugging approaches for DataLoader.

---

## Quick Diagnosis

### Check Current Status

```sql
-- Tables by status
SELECT last_status, COUNT(*)
FROM control.table_control
WHERE is_active = true
GROUP BY last_status;

-- Recent failures
SELECT
    source_table_name,
    last_status,
    last_status_message,
    last_status_date_time
FROM control.table_control
WHERE last_status = 'Failed'
ORDER BY last_status_date_time DESC
LIMIT 10;
```

### Log Locations

| System | Location |
|--------|----------|
| Dagster | Dagster Cloud UI → Runs → Select run → Logs |
| Databricks | Databricks Workspace → Jobs → Select job → Runs |
| Lakebase | `historical_metadata.log_message` |

---

## Common Issues

### 1. Table Stuck in "Queued"

**Symptoms**:
- Status = "Queued" for extended period
- No Dagster run triggered

**Causes**:
1. `master_sensor` not running
2. Table filtered out by sensor query
3. Dagster Cloud issue

**Diagnosis**:
```sql
-- Check table configuration
SELECT
    is_active,
    next_load_date_time,
    last_status,
    load_cron
FROM control.table_control
WHERE source_table_name = 'your_table';

-- Verify meets sensor criteria
SELECT *
FROM control.table_control
WHERE is_active = true
  AND next_load_date_time <= CURRENT_TIMESTAMP
  AND (last_status IS NULL OR last_status NOT IN ('Queued', 'In Progress'))
  AND source_table_name = 'your_table';
```

**Solutions**:
1. Verify `is_active = true`
2. Check `next_load_date_time` is in the past
3. Verify sensor is running in Dagster Cloud
4. Check Dagster Cloud for deployment issues

---

### 2. Table Stuck in "In Progress"

**Symptoms**:
- Status = "In Progress" for > 3 hours
- Databricks job may have completed/failed

**Causes**:
1. Databricks job failed without status update
2. Pipes communication issue
3. Status update failed

**Diagnosis**:
```sql
-- Check how long it's been running
SELECT
    source_table_name,
    last_load_date_time,
    EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - last_load_date_time))/60 as minutes_running,
    last_dagster_run,
    last_databricks_run
FROM control.table_control
WHERE last_status = 'In Progress'
ORDER BY last_load_date_time;
```

**Solutions**:
1. Check Databricks job status via `last_databricks_run` URL
2. Check Dagster run status via `last_dagster_run` URL
3. Wait for `longrunning_monitor` (180 min threshold) to auto-cancel
4. Manually reset status if needed:
   ```sql
   UPDATE control.table_control
   SET last_status = 'Failed',
       last_status_message = 'Manual reset - stuck in progress'
   WHERE source_table_name = 'your_table';
   ```

---

### 3. Connection Timeout

**Symptoms**:
- Error: "Connection timeout" or "Read timed out"
- Failure during source query

**Causes**:
1. Source database overloaded
2. Network connectivity issue
3. Query too slow

**Diagnosis**:
```sql
-- Check the error message
SELECT last_status_message
FROM control.table_control
WHERE source_table_name = 'your_table';
```

**Solutions**:
1. Test source connectivity from Databricks
2. Add partitioning for large tables:
   ```sql
   UPDATE control.table_control
   SET partition_column = 'id',
       lower_bound = '1',
       upper_bound = '1000000',
       num_partitions = 8
   WHERE source_table_name = 'your_table';
   ```
3. Reduce query complexity
4. Check source database performance

---

### 4. Out of Memory

**Symptoms**:
- Error: "OutOfMemoryError" or "Java heap space"
- Job fails during data processing

**Causes**:
1. Table too large for single load
2. Insufficient cluster memory
3. Data skew

**Solutions**:
1. Switch to `chunked_backfill` for initial load
2. Add partitioning for ongoing loads
3. Increase cluster size
4. Reduce `num_partitions` to limit concurrent reads

---

### 5. Primary Key Violation

**Symptoms**:
- Error: "Duplicate key" or merge failure
- Incremental merge fails

**Causes**:
1. `primary_key_cols` not unique in source
2. Data quality issue
3. Wrong columns specified

**Diagnosis**:
```sql
-- Check for duplicates in source
SELECT primary_key_col, COUNT(*)
FROM source_table
WHERE incremental_column > 'last_value'
GROUP BY primary_key_col
HAVING COUNT(*) > 1;
```

**Solutions**:
1. Add additional columns to `primary_key_cols`
2. Fix data quality in source
3. Switch to `append_only` if duplicates are intentional
4. Add deduplication in custom query

---

### 6. Schema Mismatch

**Symptoms**:
- Error: "Schema mismatch" or column not found
- Failure during write to Unity Catalog

**Causes**:
1. Source schema changed
2. Column renamed or removed
3. Data type changed

**Solutions**:
1. DataLoader has `overwriteSchema=true` for full loads
2. For incremental, may need to force full load:
   ```sql
   UPDATE control.table_control
   SET load_full = 'true'
   WHERE source_table_name = 'your_table';
   ```
3. Manually alter destination table if needed

---

### 7. Incremental Value Not Updating

**Symptoms**:
- Same data loaded repeatedly
- `incremental_value` stays constant

**Causes**:
1. Load failing before value update
2. No new data matching filter
3. Value update SQL error

**Diagnosis**:
```sql
-- Check current incremental value
SELECT incremental_column, incremental_value
FROM control.table_control
WHERE source_table_name = 'your_table';

-- Check if there's data beyond current value
SELECT MAX(incremental_column)
FROM source_table;
```

**Solutions**:
1. Verify load is succeeding (check status)
2. Manually update value if needed:
   ```sql
   UPDATE control.table_control
   SET incremental_value = '2024-01-20 00:00:00'
   WHERE source_table_name = 'your_table';
   ```
3. Check DataLoader logs for update errors

---

### 8. AI Retry Not Working

**Symptoms**:
- Failed tables not being retried
- AI analysis not triggering

**Causes**:
1. `retry_count >= 3` (max retries reached)
2. `failed_monitor` not running
3. AI service unavailable

**Diagnosis**:
```sql
-- Check retry count
SELECT source_table_name, retry_count, last_status_message
FROM control.table_control
WHERE last_status = 'Failed';
```

**Solutions**:
1. Manually reset for retry:
   ```sql
   UPDATE control.table_control
   SET last_status = NULL,
       retry_count = 0
   WHERE source_table_name = 'your_table';
   ```
2. Check `failed_monitor` in Dagster Cloud
3. Review AI analysis in error message prefix

---

## Debugging Steps

### Isolate Single Table

1. Disable threading to debug one table:
   ```python
   loader = DataLoader(
       db_type=db_type,
       database_config=config,
       use_threading=False  # Process one at a time
   )
   ```

2. Filter to specific table:
   ```python
   loader.process_tables([single_table_config])
   ```

### Test Source Connectivity

```python
# In Databricks notebook
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

# Test JDBC connection
df = spark.read.format("jdbc") \
    .option("url", "jdbc:sqlserver://host:1433;databaseName=db") \
    .option("query", "SELECT TOP 10 * FROM table") \
    .option("user", "user") \
    .option("password", "pass") \
    .load()

df.show()
```

### Check DataLoader Results

```python
# After execution
update_list, errors_list, completed_list = loader.get_results()

# Print errors
for error in errors_list:
    print(f"Table: {error['table_name']}")
    print(f"Error: {error['error_message']}")
    print(f"Traceback: {error['error_traceback']}")
```

---

## Query Recipes

### Find All Failures Today

```sql
SELECT
    source_table_name,
    last_status_message,
    last_status_date_time,
    last_dagster_run
FROM control.table_control
WHERE last_status = 'Failed'
  AND last_status_date_time >= CURRENT_DATE;
```

### Check Execution History

```sql
SELECT
    source_table,
    load_status,
    rows_processed,
    total_duration,
    log_message,
    load_start_time
FROM control.historical_metadata
WHERE source_table = 'your_table'
ORDER BY load_start_time DESC
LIMIT 10;
```

### Find Long-Running Loads

```sql
SELECT
    source_table_name,
    last_load_date_time,
    EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - last_load_date_time))/60 as minutes_running
FROM control.table_control
WHERE last_status = 'In Progress'
ORDER BY last_load_date_time;
```

### Reset All Failed for Database

```sql
UPDATE control.table_control
SET last_status = NULL
WHERE last_status = 'Failed'
  AND db_config_key = 'your_db_config';
```

### Check Recent Changes

```sql
SELECT *
FROM control.table_control_history_vw
WHERE table_name = 'your_table'
ORDER BY changed_at DESC
LIMIT 5;
```

---

## Related Documentation

- [System Overview](01-system-overview.md) - Architecture context
- [Lakebase Reference](02-lakebase-control-database.md) - Control table queries
- [Dagster Orchestration](03-dagster-orchestration.md) - Sensor details
- [Load Strategies](05-load-strategies.md) - Strategy configuration
