# System Overview

## What is DataLoader?

DataLoader is a production data orchestration system that loads data from source databases into a Databricks Unity Catalog. It provides:

- **Multi-database connectivity**: SQL Server, Oracle, PostgreSQL, Snowflake, ClickHouse
- **Flexible load strategies**: Full, incremental, append-only, rolling window, and chunked backfill
- **Automated orchestration**: Sensor-based scheduling via Dagster Cloud
- **Fault tolerance**: Automatic retry analysis and timeout monitoring
- **Execution tracking**: Complete audit trail of all data loads

---

## Key Concepts

### Control Plane vs Data Plane

DataLoader separates configuration from execution:

| Plane | Component | Responsibility |
|-------|-----------|----------------|
| **Control Plane** | Lakebase | Stores what to load, when, and how |
| **Orchestration** | Dagster | Monitors control plane, triggers jobs |
| **Data Plane** | Databricks | Executes loads, moves actual data |
| **Destination** | Unity Catalog | Stores loaded data in bronze layer |

### Sensor-Driven Architecture

Rather than scheduled jobs, DataLoader uses **sensors** that continuously poll for work:

1. Sensors query Lakebase every 60 seconds
2. Find tables where `next_load_date_time <= NOW()`
3. Yield RunRequests for each ready table
4. Jobs execute in parallel on Databricks

### Incremental Value Tracking

For incremental loads, DataLoader tracks the last processed value:

1. Before load: Query `incremental_value` from `table_control`
2. During load: Filter `WHERE column > incremental_value`
3. After load: Update `incremental_value` with `MAX(column)` from destination

---

## System Components

### 1. Lakebase (Control Database)

PostgreSQL database storing all configuration and state.

| Table | Purpose |
|-------|---------|
| `table_control` | What tables to load, how, and when |
| `table_control_dbconfig` | Database connection configurations |
| `historical_metadata` | Execution history and metrics |
| `table_control_history` | Audit trail of configuration changes |

**Environments**:
- Production: `dataloader` database
- Pre-production: `dataloader_test` database

### 2. Dagster Cloud (Orchestration)

Cloud-hosted Dagster deployment with sensors and assets.

**Sensors** (5 total):
| Sensor | Interval | Purpose |
|--------|----------|---------|
| `master_sensor` | 60s | Trigger table loads |
| `longqueued_monitor` | 10min | Cancel stuck queued jobs (>60min) |
| `longrunning_monitor` | 10min | Cancel stuck running jobs (>180min) |
| `failed_monitor` | 15min | AI retry analysis for failures |
| `landing_table_monitor` | 30min | Generate schema files for new tables |

**Assets** (2 total):
| Asset | Triggered By | Purpose |
|-------|--------------|---------|
| `dataloader_table_load` | master_sensor | Execute data load |
| `dataloader_retry_analysis` | failed_monitor | Analyze failures, recommend retry |

### 3. Databricks (Execution Engine)

Spark-based execution environment.

**Components**:
- **DataLoader class**: Core loading logic (`dbx/functions/dataloader.py`)
- **Pipes scripts**: Dagster integration (`dbx/notebooks/dataloader/`)
- **Azure Key Vault**: Secrets management for database credentials

**Execution model**:
- Dagster Pipes submits tasks to Databricks
- DataLoader class executes with Spark JDBC/native connectors
- Bidirectional communication reports progress back to Dagster

### 4. Unity Catalog (Destination)

Databricks Unity Catalog for data governance.

**Structure**:
- **Catalog**: Typically `bronze` for raw ingested data
- **Schema**: Organized by source system or domain
- **Tables**: Delta format with schema evolution

---

## End-to-End Data Flow

```
1. Configuration
   └── User defines table in Lakebase (via SQL or UI)
       ├── Source: schema.table
       ├── Destination: catalog.schema.table
       ├── Strategy: incremental
       └── Schedule: 0 2 * * * (daily at 2 AM)

2. Scheduling
   └── master_sensor runs every 60 seconds
       ├── Queries Lakebase for ready tables
       ├── Filters: is_active=true, next_load_date_time <= NOW
       └── Yields RunRequest for each table

3. Orchestration
   └── Dagster materializes dataloader_table_load asset
       ├── Updates status to 'In Progress'
       ├── Submits Databricks task via Pipes
       └── Waits for completion

4. Execution
   └── dataloader_pipe.py runs on Databricks
       ├── Loads credentials from Key Vault
       ├── Initializes DataLoader class
       ├── Queries last incremental_value
       └── Builds filtered query

5. Data Movement
   └── DataLoader.load_table_from_source()
       ├── Executes JDBC/native query against source
       ├── Transforms column names
       └── Returns Spark DataFrame

6. Write to Destination
   └── DataLoader.write_to_unity_catalog()
       ├── Creates staging table (for merge)
       ├── Merges by primary keys (upsert)
       ├── Drops staging table
       └── Updates incremental_value

7. Status Update
   └── Pipes script updates Lakebase
       ├── status = 'Succeeded'
       ├── rows_processed = count
       ├── incremental_value = MAX(column)
       └── historical_metadata record
```

---

## Database Support

| Type | Connection | Driver | Notes |
|------|------------|--------|-------|
| `mssql` | JDBC | SQLServerDriver | Azure AD service principal supported |
| `oracle` | JDBC | OracleDriver | Timezone-aware with date handling |
| `postgresql` | JDBC | PostgreSQL Driver | Standard JDBC |
| `snowflake` | Native | Spark connector | Password authentication |
| `snowflake_pem` | Native | Spark connector | Key-pair authentication |
| `clickhouse` | JDBC | ClickHouseDriver | HTTP protocol, custom type mapping |

---

## Load Strategies

| Strategy | Use Case | Behavior |
|----------|----------|----------|
| `full` | Small tables, no tracking | Truncate and reload |
| `incremental` | Large tables with timestamps | Merge by primary key |
| `append_only` | Immutable event data | Append without deduplication |
| `rolling` | Time-windowed data | Merge + delete outside window |
| `check_and_load` | Conditional loading | Only load if changes exist |
| `chunked_backfill` | Large initial loads | Sequential chunks, then switch to incremental |

See [Load Strategies Guide](05-load-strategies.md) for detailed configuration.

---

## Quick Reference

### File Locations

| File | Purpose |
|------|---------|
| `dbx/functions/dataloader.py` | DataLoader class |
| `dbx/notebooks/dataloader/dataloader_pipe.py` | Dagster Pipes script |
| `dagsters/sensors/` | Dagster sensors |
| `dagsters/assets/dataloader_pipes_asset.py` | Dagster assets |
| `dagsters/utils/lakebase_client.py` | Lakebase database client |
| `sql/01_create_tables.sql` | Control table schemas |

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `LAKEBASE_HOST` | Lakebase PostgreSQL host |
| `LAKEBASE_PORT` | Lakebase PostgreSQL port |
| `LAKEBASE_DATABASE` | Database name |
| `LAKEBASE_USER` | Database user |
| `LAKEBASE_PASSWORD` | Database password |
| `DATABRICKS_CLUSTER_ID` | Target Databricks cluster |

### Commands

```bash
# Type check
uvx pyrefly check --summarize-errors

# Lint
uv run ruff check .

# Run Dagster locally
dagster dev
```

---

## Related Documentation

- [Architecture Diagrams](../diagrams/02-architecture.md) - Visual component diagrams
- [Sequence Diagrams](../diagrams/03-sequence-diagrams.md) - Operation flows
- [Lakebase Reference](02-lakebase-control-database.md) - Control table details
- [Dagster Reference](03-dagster-orchestration.md) - Sensor and asset details
- [DataLoader Reference](04-dataloader-class.md) - Class methods
- [Load Strategies](05-load-strategies.md) - Strategy configuration
- [Troubleshooting](06-troubleshooting.md) - Debugging guide
