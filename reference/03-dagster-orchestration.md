# Dagster Orchestration

Dagster orchestrates the DataLoader system through sensors and assets.

---

## Architecture

```
Dagster
├── Sensors (5) ────── Continuously poll Lakebase for work
│   ├── master_sensor
│   ├── longqueued_monitor
│   ├── longrunning_monitor
│   ├── failed_monitor
│   └── landing_table_monitor
│
├── Assets (2) ─────── Execute data loading via Databricks Pipes
│   ├── dataloader_table_load
│   └── dataloader_retry_analysis
│
└── Resources ──────── Shared connections and clients
    ├── lakebase (PostgreSQL)
    ├── pipes_databricks (Pipes client)
    └── databricks_rest (SQL connector)
```

---

## Sensors

### master_sensor

**Purpose**: Primary orchestration trigger that discovers tables ready to load.

| Property | Value |
|----------|-------|
| **Interval** | 60 seconds |
| **Batch Size** | 10 tables per tick |
| **Asset** | `dataloader_table_load` |

**Query Criteria**:
```sql
SELECT * FROM table_control
WHERE is_active = true
  AND next_load_date_time <= NOW()
  AND (last_status IS NULL OR last_status NOT IN ('Queued', 'In Progress'))
```

**Actions per table**:
1. Insert `historical_metadata` record (queue time)
2. Update `table_control.last_status = 'Queued'`
3. Yield `RunRequest` with table configuration

**RunRequest Config**:
```python
{
    "ops": {
        "dataloader__table_load": {
            "config": {
                "config_id": int,
                "control_key": str,
                "metadata_id": int,
                "source_catalog": str,
                "source_schema_name": str,
                "source_table_name": str,
                "destination_catalog": str,
                "destination_schema_name": str,
                "destination_table_name": str,
                "db_type": str,
                "db_config_value": dict,
                "db_kv_scope": str,
                "load_strategy": str,
                "incremental_column": str,
                "incremental_value": str,
                # ... additional fields
            }
        }
    }
}
```

---

### longqueued_monitor

**Purpose**: Detect and cancel jobs stuck in "Queued" status.

| Property | Value |
|----------|-------|
| **Interval** | 10 minutes |
| **Threshold** | 60 minutes |
| **Action** | Mark as Failed |

**Query**:
```sql
SELECT * FROM table_control
WHERE last_status = 'Queued'
  AND last_status_date_time < NOW() - INTERVAL '60 minutes'
```

**Actions**:
- Updates status to `Failed`
- Sets message: "Cancelled - stuck in queue for >60 minutes"
- No RunRequest (passive monitoring only)

---

### longrunning_monitor

**Purpose**: Detect and terminate jobs stuck in "In Progress" status.

| Property | Value |
|----------|-------|
| **Interval** | 10 minutes |
| **Threshold** | 180 minutes |
| **Action** | Cancel run + Mark as Failed |

**Query**:
```sql
SELECT * FROM table_control
WHERE last_status = 'In Progress'
  AND last_load_date_time < NOW() - INTERVAL '180 minutes'
```

**Actions**:
1. Extract Dagster run ID from `last_dagster_run` URL
2. Call `instance.run_coordinator.cancel_run(run_id)`
3. Update status to `Failed`
4. Set message: "Cancelled - exceeded 180 minute timeout"

---

### failed_monitor

**Purpose**: Analyze failures and recommend retry vs manual review.

| Property | Value |
|----------|-------|
| **Interval** | 15 minutes |
| **Max Retries** | 3 |
| **Asset** | `dataloader_retry_analysis` |

**Query**:
```sql
SELECT * FROM table_control
WHERE last_status = 'Failed'
  AND retry_count < 3
```

**Actions**:
- Yields `RunRequest` for `dataloader_retry_analysis` asset
- AI analyzes error message
- Decision: `reset_for_retry()` or `mark_for_review()`

---

### landing_table_monitor

**Purpose**: Generate schema files for newly loaded tables.

| Property | Value |
|----------|-------|
| **Interval** | 30 minutes |
| **Job** | `landing_table_git_job` |

**Query**:
```sql
SELECT * FROM table_control
WHERE last_status = 'Succeeded'
  AND staging_model_processed = false
```

**Actions**:
- Batches all unprocessed tables
- Yields `RunRequest` for Git job
- Creates schema files for downstream models
- Marks `staging_model_processed = true` on success

---

## Assets

### dataloader_table_load

**Purpose**: Execute a single table load on Databricks.

**Triggered by**: `master_sensor`

**Config Class**: `DataloaderTableConfig`

```python
@asset(
    name="dataloader__table_load",
    required_resource_keys={"lakebase", "pipes_databricks"}
)
def dataloader_table_load(context, config: DataloaderTableConfig):
    # 1. Update status to 'In Progress'
    context.resources.lakebase.update_control_status(
        config_id=config.config_id,
        status="In Progress",
        dagster_run_url=context.run.run_id
    )

    # 2. Submit Databricks task via Pipes
    task_config = {
        "task_key": "dataloader_task",
        "spark_python_task": {
            "python_file": "/Workspace/Repos/.../dataloader_pipe.py"
        },
        "existing_cluster_id": cluster_id,
        "timeout_seconds": 10800  # 3 hours
    }

    # 3. Execute and capture result
    result = context.resources.pipes_databricks.run(
        task=task_config,
        extras={"params": config.dict()}
    )

    return result.materialize_result
```

**Databricks Task Configuration**:

| Property | Value |
|----------|-------|
| **Script** | `dataloader_pipe.py` |
| **Timeout** | 10,800 seconds (3 hours) |
| **Libraries** | `dagster-pipes` |

---

### dataloader_retry_analysis

**Purpose**: AI-powered analysis of failed loads.

**Triggered by**: `failed_monitor`

**Execution**:
1. Submits `dataloader_retry_pipe.py` to Databricks
2. AI analyzes error message
3. Returns decision with confidence level

**Decisions**:

| Decision | Action | Effect |
|----------|--------|--------|
| `Retry Recommended` | `reset_for_retry()` | Status = NULL, eligible for next pickup |
| `Requires Review` | `mark_for_review()` | Status = Failed with marker |

**Message Prefixes**:
- `[AI Analysis - Retry Recommended]`
- `[AI Analysis - Requires Review]`

---

## Resources

### LakebaseResource

PostgreSQL connection to Lakebase control database.

```python
@resource
def lakebase_resource(context):
    return LakebaseClient(
        host=os.getenv("LAKEBASE_HOST"),
        port=os.getenv("LAKEBASE_PORT"),
        database=os.getenv("LAKEBASE_DATABASE"),
        user=os.getenv("LAKEBASE_USER"),
        password=os.getenv("LAKEBASE_PASSWORD")
    )
```

**Key Methods**:

| Method | Purpose |
|--------|---------|
| `get_control_tables()` | Query ready tables |
| `get_table_details(control_key)` | Get single table config |
| `update_control_status(config_id, status, message)` | Update status |
| `update_run_tracking(config_id, dagster_url, databricks_url)` | Set run URLs |
| `insert_initial_metadata(...)` | Create history record at queue |
| `update_metadata(metadata_id, ...)` | Update with results |
| `reset_for_retry(config_id, reason)` | Reset for retry |
| `mark_for_review(config_id, reason)` | Flag for manual review |

### DatabricksPipesResource

Dagster Pipes client for Databricks execution.

```python
@resource
def pipes_databricks_resource(context):
    return PipesDatabricksClient(
        client=WorkspaceClient(),
        context_injector=DbfsContextInjector(client=dbfs_client),
        message_reader=DbfsMessageReader(client=dbfs_client)
    )
```

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `DAGSTER_ENVIRONMENT` | `npd` or `prod` |
| `LAKEBASE_HOST` | PostgreSQL host |
| `LAKEBASE_PORT` | PostgreSQL port (usually 5432) |
| `LAKEBASE_DATABASE` | `dataloader` or `dataloader_test` |
| `LAKEBASE_USER` | Database user |
| `LAKEBASE_PASSWORD` | Database password |
| `DATABRICKS_CLUSTER_ID` | Target cluster for loads |
| `DATABRICKS_WORKSPACE_URL` | Workspace URL |

---

## Bidirectional Traceability

Every load maintains links between systems:

| Location | Field | Example |
|----------|-------|---------|
| Lakebase | `last_dagster_run` | `https://dagster.cloud/org/runs/abc123` |
| Lakebase | `last_databricks_run` | `https://adb-xxx.azuredatabricks.net/jobs/123/runs/456` |
| Dagster | Run metadata | Links to Databricks job |
| Databricks | Job tags | Links to Dagster run |

---

## File Locations

| File | Purpose |
|------|---------|
| `dagsters/definitions.py` | Central Dagster configuration |
| `dagsters/sensors/dataloader_master_sensor.py` | Main trigger sensor |
| `dagsters/sensors/dataloader_longqueued_monitor.py` | Queue timeout monitor |
| `dagsters/sensors/dataloader_longrunning_monitor.py` | Run timeout monitor |
| `dagsters/sensors/dataloader_failed_monitor.py` | Failure analysis sensor |
| `dagsters/sensors/dataloader_landing_table_monitor.py` | Schema generation sensor |
| `dagsters/assets/dataloader_pipes_asset.py` | Load and retry assets |
| `dagsters/utils/lakebase_client.py` | Database client |
| `dagsters/resources/lakebase.py` | Lakebase resource |
| `dagsters/resources/pipes_databricks.py` | Pipes resource |

---

## Related Documentation

- [System Overview](01-system-overview.md) - High-level architecture
- [Lakebase Reference](02-lakebase-control-database.md) - Control tables
- [Sequence Diagrams](../diagrams/03-sequence-diagrams.md) - Sensor flows
- [Troubleshooting](06-troubleshooting.md) - Debugging sensor issues
