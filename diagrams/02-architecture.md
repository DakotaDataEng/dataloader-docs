# Architecture Diagrams

Detailed Mermaid diagrams showing DataLoader system architecture.

---

## 1. System Context Diagram

Shows the DataLoader system boundary and external interactions.

```mermaid
flowchart TB
    subgraph External["External Systems"]
        Sources["Source Databases<br/>(SQL Server, Oracle,<br/>PostgreSQL, Snowflake,<br/>ClickHouse)"]
        KeyVault["Azure Key Vault<br/>(Secrets)"]
        Users["Data Engineers<br/>(Configuration)"]
    end

    subgraph DataLoader["DataLoader System"]
        Lakebase["Lakebase<br/>Control Database"]
        Dagster["Dagster<br/>Orchestration"]
        Databricks["Databricks<br/>Execution"]
    end

    subgraph Destination["Data Platform"]
        Unity["Unity Catalog<br/>(Bronze Layer)"]
    end

    Users -->|Configure tables| Lakebase
    Lakebase <-->|Config & Status| Dagster
    Dagster -->|Trigger jobs| Databricks
    Sources -->|JDBC/Native| Databricks
    KeyVault -->|Credentials| Databricks
    Databricks -->|Write data| Unity
    Databricks -.->|Status updates| Lakebase
```

---

## 2. Component Diagram

Shows all internal components and their relationships.

```mermaid
flowchart TB
    subgraph Lakebase["Lakebase PostgreSQL"]
        TC["table_control<br/>─────────────<br/>config_id, control_key<br/>source/destination tables<br/>load_strategy, cron<br/>last_status, incremental_value"]
        TCDB["table_control_dbconfig<br/>─────────────<br/>db_config_key<br/>db_type, db_config_value<br/>db_kv_scope"]
        HM["historical_metadata<br/>─────────────<br/>control_key, run_id<br/>load_queued/start/end_time<br/>rows_processed, load_status"]
        TCH["table_control_history<br/>─────────────<br/>control_key, changed_at<br/>change_type, change_source<br/>old_values, new_values"]

        TCDB --> TC
        TC --> HM
        TC --> TCH
    end

    subgraph Dagster["Dagster"]
        subgraph Sensors["Sensors"]
            MS["master_sensor<br/>(60s interval)"]
            LQM["longqueued_monitor<br/>(10min, 60min threshold)"]
            LRM["longrunning_monitor<br/>(10min, 180min threshold)"]
            FM["failed_monitor<br/>(15min interval)"]
            LTM["landing_table_monitor<br/>(30min interval)"]
        end

        subgraph Assets["Assets"]
            ATL["dataloader_table_load"]
            ARA["dataloader_retry_analysis"]
        end

        MS -->|RunRequest| ATL
        FM -->|RunRequest| ARA
    end

    subgraph Databricks["Databricks"]
        DLP["dataloader_pipe.py"]
        DL["DataLoader Class<br/>─────────────<br/>process_tables()<br/>load_table_from_source()<br/>write_to_unity_catalog()"]
        DRP["dataloader_retry_pipe.py"]

        DLP --> DL
    end

    TC <-->|Query/Update| MS
    TC <-->|Query/Update| LQM
    TC <-->|Query/Update| LRM
    TC <-->|Query/Update| FM
    TC <-->|Query| LTM

    ATL -->|Pipes| DLP
    ARA -->|Pipes| DRP

    DL -.->|Status| HM
```

---

## 3. Data Flow Diagram

Shows how data and control signals flow through the system.

```mermaid
flowchart TB
    subgraph Config["Configuration"]
        SQL["SQL / dl-app UI"]
    end

    subgraph Control["Control Plane"]
        TCDB["dbconfig"]
        TC["table_control"]
        HM["historical_metadata"]
        TCDB -->|Connection config| TC
    end

    subgraph Orchestration["Orchestration"]
        Sensor["master_sensor (every 60s)"]
        Asset["dataloader_table_load"]
        Sensor -->|RunRequest| Asset
    end

    subgraph Execution["Execution"]
        Pipes["Dagster Pipes"]
        DL["DataLoader"]
        Pipes -->|Execute| DL
    end

    subgraph Sources["Source DBs"]
        direction LR
        MSSQL["SQL Server"]
        Oracle["Oracle"]
        PG["PostgreSQL"]
        SF["Snowflake"]
        CH["ClickHouse"]
    end

    subgraph Destination["Destination"]
        UC["Unity Catalog (Bronze)"]
    end

    SQL -->|INSERT/UPDATE| TC
    TC -->|Ready tables| Sensor
    Asset -->|Submit job| Pipes
    Sources -->|JDBC/Native| DL
    DL -->|Write| UC
    DL -.->|rows, status| HM
    DL -.->|incremental_value| TC

    style Sources fill:#4A90A4
    style Destination fill:#2E8B57
    style Control fill:#6B8E23
    style Orchestration fill:#9370DB
    style Execution fill:#FF6B35
```

---

## 4. Sensor Orchestration Diagram

Shows all 5 sensors and their responsibilities.

```mermaid
flowchart TB
    subgraph Sensors["Dagster Sensors"]
        MS["master_sensor<br/>───────────<br/>Interval: 60 seconds<br/>Batch: 10 tables/tick"]
        LQM["longqueued_monitor<br/>───────────<br/>Interval: 10 minutes<br/>Threshold: 60 min queued"]
        LRM["longrunning_monitor<br/>───────────<br/>Interval: 10 minutes<br/>Threshold: 180 min running"]
        FM["failed_monitor<br/>───────────<br/>Interval: 15 minutes<br/>Max retries: 3"]
        LTM["landing_table_monitor<br/>───────────<br/>Interval: 30 minutes<br/>Creates schema files"]
    end

    subgraph TableStates["Table States"]
        Ready["Ready<br/>(is_active=true,<br/>next_load_date_time <= NOW,<br/>status NOT Queued/In Progress)"]
        Queued["Queued"]
        InProgress["In Progress"]
        Succeeded["Succeeded"]
        Failed["Failed"]
    end

    subgraph Actions["Actions"]
        TriggerLoad["Trigger<br/>dataloader_table_load"]
        CancelQueued["Mark Failed<br/>(cancelled - stuck in queue)"]
        CancelRunning["Cancel Dagster Run<br/>Mark Failed"]
        AIAnalysis["Trigger<br/>dataloader_retry_analysis"]
        GitJob["Trigger<br/>landing_table_git_job"]
    end

    MS -->|Query| Ready
    Ready -->|yield RunRequest| TriggerLoad
    TriggerLoad --> Queued
    Queued --> InProgress
    InProgress --> Succeeded
    InProgress --> Failed

    LQM -->|Query status=Queued > 60min| Queued
    LQM --> CancelQueued
    CancelQueued --> Failed

    LRM -->|Query status=In Progress > 180min| InProgress
    LRM --> CancelRunning
    CancelRunning --> Failed

    FM -->|Query status=Failed, retry_count < 3| Failed
    FM --> AIAnalysis
    AIAnalysis -->|Retry| Ready
    AIAnalysis -->|Manual Review| Failed

    LTM -->|Query staging_model_processed=false| Succeeded
    LTM --> GitJob
```

---

## 5. Load Strategy State Machine

Shows the 6 load strategies and their behaviors.

```mermaid
stateDiagram-v2
    [*] --> ConfigureStrategy: Define table in table_control

    state ConfigureStrategy {
        [*] --> SelectStrategy
        SelectStrategy --> full: Small tables, no tracking
        SelectStrategy --> incremental: Large tables, timestamp/ID tracking
        SelectStrategy --> append_only: Immutable data, logs
        SelectStrategy --> rolling: Time-windowed data
        SelectStrategy --> check_and_load: Conditional loading
        SelectStrategy --> chunked_backfill: Large initial loads
    }

    state full {
        f1: Truncate destination
        f2: Load all rows from source
        f3: Write with overwrite mode
        f1 --> f2
        f2 --> f3
    }

    state incremental {
        i1: Query last incremental_value
        i2: Filter WHERE col > last_value
        i3: Merge upsert by primary keys
        i4: Update incremental_value
        i1 --> i2
        i2 --> i3
        i3 --> i4
    }

    state append_only {
        a1: Query last incremental_value
        a2: Filter WHERE col > last_value
        a3: Append (no merge)
        a4: Update incremental_value
        a1 --> a2
        a2 --> a3
        a3 --> a4
    }

    state rolling {
        r1: Calculate window (today - N days)
        r2: Filter by rolling_column
        r3: Merge + delete outside window
        r1 --> r2
        r2 --> r3
    }

    state check_and_load {
        c1: Query COUNT(*) of changes
        c2: If count > 0, execute incremental
        c3: If count = 0, skip load
        c1 --> c2
        c1 --> c3
    }

    state chunked_backfill {
        cb1: Query source MIN/MAX
        cb2: Calculate chunk boundaries
        cb3: Process chunks sequentially
        cb4: First chunk: overwrite
        cb5: Subsequent: append
        cb6: Update incremental_value at end
        cb1 --> cb2
        cb2 --> cb3
        cb3 --> cb4
        cb4 --> cb5
        cb5 --> cb6
    }

    chunked_backfill --> incremental: After backfill complete,<br/>switch to incremental

    full --> [*]: Load complete
    incremental --> [*]: Load complete
    append_only --> [*]: Load complete
    rolling --> [*]: Load complete
    check_and_load --> [*]: Load complete
    chunked_backfill --> [*]: Load complete
```

---

## 6. Status Lifecycle

Shows table status transitions during execution.

```mermaid
stateDiagram-v2
    [*] --> NULL: Table created

    NULL --> Queued: master_sensor<br/>picks up ready table

    Queued --> InProgress: Asset execution starts
    Queued --> Failed: Stuck > 60min<br/>(longqueued_monitor)

    InProgress --> Succeeded: DataLoader completes
    InProgress --> Failed: Error during load
    InProgress --> Failed: Stuck > 180min<br/>(longrunning_monitor)

    Succeeded --> NULL: Next cron trigger

    Failed --> NULL: AI retry recommended
    Failed --> Failed: AI manual review

    state Queued {
        [*] --> WaitingForAsset
        WaitingForAsset: historical_metadata created
        WaitingForAsset: load_queued_time set
    }

    state InProgress {
        [*] --> Executing
        Executing: DataLoader.process_tables()
        Executing: load_start_time set
    }

    state Succeeded {
        [*] --> Complete
        Complete: rows_processed logged
        Complete: load_end_time set
        Complete: incremental_value updated
    }

    state Failed {
        [*] --> ErrorLogged
        ErrorLogged: last_status_message set
        ErrorLogged: retry_count incremented
    }
```

---

## 7. Database Connection Flow

Shows how credentials are resolved and connections established.

```mermaid
flowchart LR
    subgraph Config["table_control_dbconfig"]
        DBConfig["db_config_value (JSONB)<br/>───────────<br/>{host: 'kv-secret-name',<br/>port: 'kv-port-name',<br/>user: 'kv-user-name',<br/>password: 'kv-pass-name'}"]
        Scope["db_kv_scope<br/>'azure-keyvault'"]
    end

    subgraph KeyVault["Azure Key Vault"]
        Secrets["Actual credentials"]
    end

    subgraph Databricks["Databricks Runtime"]
        DBUtils["dbutils.secrets.get()"]
        Resolved["database_config dict<br/>───────────<br/>{host: 'actual-host.com',<br/>port: '1433',<br/>user: 'dataloader',<br/>password: '***'}"]
    end

    subgraph DataLoader["DataLoader Class"]
        ConnStr["set_connection_string()"]
        JDBC["JDBC URL or<br/>Snowflake native config"]
    end

    DBConfig --> DBUtils
    Scope --> DBUtils
    DBUtils --> KeyVault
    KeyVault --> Resolved
    Resolved --> ConnStr
    ConnStr --> JDBC
```

---

## Related Documentation

- [Sequence Diagrams](03-sequence-diagrams.md) - Step-by-step operation flows
- [Lakebase Reference](../reference/02-lakebase-control-database.md) - Control table details
- [Dagster Reference](../reference/03-dagster-orchestration.md) - Sensor and asset details
- [DataLoader Reference](../reference/04-dataloader-class.md) - Class methods and database support
