# DataLoader Documentation

Production data orchestration system that loads data from source databases into Databricks Unity Catalog.

## Quick Navigation

| Audience | Start Here |
|----------|------------|
| Executive/Stakeholder | [System Overview Diagram](diagrams/01-system-overview.svg) |
| Architect | [Architecture Diagrams](diagrams/02-architecture.md) |
| Developer | [System Overview](reference/01-system-overview.md) |
| Operator | [Troubleshooting Guide](reference/06-troubleshooting.md) |

---

## System at a Glance

```
Source Databases          Orchestration              Execution              Destination
-----------------         -------------              ---------              -----------
SQL Server    ─┐
Oracle        ─┼─►  Lakebase  ◄──►  Dagster  ──►  Databricks  ──►  Unity Catalog
PostgreSQL    ─┤    (Control)       (Sensors)      (Spark)          (Bronze)
Snowflake     ─┤
ClickHouse    ─┘
```

---

## Diagrams

| File | Description |
|------|-------------|
| [01-system-overview.svg](diagrams/01-system-overview.svg) | High-level visual showing all components and flows |
| [02-architecture.md](diagrams/02-architecture.md) | Mermaid diagrams: components, data flow, sensors, state machines |
| [03-sequence-diagrams.md](diagrams/03-sequence-diagrams.md) | Step-by-step sequences for key operations |

---

## Reference Documentation

| File | Description |
|------|-------------|
| [01-system-overview.md](reference/01-system-overview.md) | What is DataLoader, key concepts, end-to-end flow |
| [02-lakebase-control-database.md](reference/02-lakebase-control-database.md) | Control tables: table_control, dbconfig, metadata, history |
| [03-dagster-orchestration.md](reference/03-dagster-orchestration.md) | Sensors, assets, jobs, Databricks Pipes integration |
| [04-dataloader-class.md](reference/04-dataloader-class.md) | DataLoader class: methods, database support, threading |
| [05-load-strategies.md](reference/05-load-strategies.md) | 6 strategies: full, incremental, append_only, rolling, check_and_load, chunked_backfill |
| [06-troubleshooting.md](reference/06-troubleshooting.md) | Common issues, debugging steps, query recipes |
| [07-control-manager-ui.md](reference/07-control-manager-ui.md) | dl-app web UI: dashboard, bulk edit, promotion workflow |

---

## Core Components

### 1. Lakebase Control Database
PostgreSQL database storing configuration and execution state.
- **Tables**: `table_control`, `table_control_dbconfig`, `historical_metadata`, `table_control_history`
- **Environments**: `dataloader` (prod), `dataloader_test` (pre-prod)

### 2. Dagster Orchestration
Sensor-based scheduling and job management.
- **5 Sensors**: master (triggers), longqueued monitor, longrunning monitor, failed monitor, landing table monitor
- **2 Assets**: `dataloader_table_load`, `dataloader_retry_analysis`

### 3. DataLoader Class
Spark-based data loading engine (`dbx/functions/dataloader.py`).
- **6 Database Types**: SQL Server, Oracle, PostgreSQL, Snowflake, Snowflake PEM, ClickHouse
- **6 Load Strategies**: full, incremental, append_only, rolling, check_and_load, chunked_backfill

### 4. Databricks Execution
Dagster Pipes integration for remote Spark execution.
- **Pipes Scripts**: `dataloader_pipe.py`, `dataloader_retry_pipe.py`
- **Destination**: Unity Catalog bronze layer

---

### 5. Control Manager UI (dl-app)
Flask web UI for managing control tables (development tool).
- **Features**: Dashboard, bulk edit, promotion workflow, history tracking
- **Documentation**: [Control Manager UI Guide](reference/07-control-manager-ui.md)

---

## Quick Links

- **Add new table**: See [Load Strategies](reference/05-load-strategies.md)
- **Debug failed load**: See [Troubleshooting](reference/06-troubleshooting.md)
- **Understand sensors**: See [Dagster Orchestration](reference/03-dagster-orchestration.md)
- **Database connection strings**: See [DataLoader Class](reference/04-dataloader-class.md)
- **Use the web UI**: See [Control Manager UI](reference/07-control-manager-ui.md)
