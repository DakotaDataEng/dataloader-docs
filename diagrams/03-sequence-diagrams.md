# Sequence Diagrams

Step-by-step sequences for key DataLoader operations.

---

## 1. Successful Table Load

Complete sequence from sensor trigger to successful completion.

```mermaid
sequenceDiagram
    participant Sensor as master_sensor
    participant Lakebase as Lakebase DB
    participant Asset as dataloader_table_load
    participant Pipes as Dagster Pipes
    participant DBX as Databricks
    participant DL as DataLoader
    participant Source as Source DB
    participant UC as Unity Catalog

    Note over Sensor: Runs every 60 seconds

    Sensor->>Lakebase: Query ready tables<br/>(is_active=true, next_load <= NOW,<br/>status NOT IN Queued/In Progress)
    Lakebase-->>Sensor: Return batch (max 10 tables)

    loop For each table
        Sensor->>Lakebase: INSERT historical_metadata<br/>(load_queued_time)
        Sensor->>Lakebase: UPDATE table_control<br/>SET status='Queued'
        Sensor->>Asset: yield RunRequest(table_config)
    end

    Asset->>Lakebase: UPDATE status='In Progress'<br/>SET dagster_run_url
    Asset->>Pipes: Submit Databricks task
    Pipes->>DBX: Execute dataloader_pipe.py

    DBX->>DBX: Load secrets from Key Vault
    DBX->>Lakebase: UPDATE databricks_run_url

    DBX->>DL: Initialize DataLoader(db_config)
    DL->>Lakebase: Query incremental_value
    Lakebase-->>DL: Return last value (or default)

    DL->>Source: Execute JDBC/Native query<br/>WHERE col > last_value
    Source-->>DL: Return DataFrame

    DL->>UC: Write to Unity Catalog<br/>(merge/append/overwrite)
    UC-->>DL: Write complete

    DL->>DL: Calculate MAX(incremental_column)

    DBX->>Lakebase: UPDATE status='Succeeded'<br/>SET incremental_value, rows_processed
    DBX->>Lakebase: UPDATE historical_metadata<br/>(load_end_time, rows_processed)

    DBX-->>Pipes: Return MaterializeResult
    Pipes-->>Asset: Execution complete
    Asset-->>Sensor: Run complete
```

---

## 2. Failed Load with AI Retry

Sequence showing failure detection and AI-powered retry analysis.

```mermaid
sequenceDiagram
    participant Asset as dataloader_table_load
    participant DBX as Databricks
    participant DL as DataLoader
    participant Source as Source DB
    participant Lakebase as Lakebase DB
    participant FM as failed_monitor
    participant Retry as dataloader_retry_analysis
    participant AI as AI Analysis

    Note over Asset,DL: Normal execution begins...

    Asset->>DBX: Execute dataloader_pipe.py
    DBX->>DL: Initialize DataLoader

    DL->>Source: Execute query
    Source--xDL: Connection timeout / Error

    DL->>DL: Catch exception<br/>Add to errors_list

    DBX->>Lakebase: UPDATE status='Failed'<br/>SET error_message
    DBX->>Lakebase: UPDATE historical_metadata<br/>SET load_status='Failed'

    Note over FM: Runs every 15 minutes

    FM->>Lakebase: Query tables WHERE<br/>status='Failed' AND retry_count < 3
    Lakebase-->>FM: Return failed tables

    FM->>Retry: yield RunRequest(failed_table)

    Retry->>DBX: Execute dataloader_retry_pipe.py
    DBX->>AI: Analyze error message

    alt Retry Recommended
        AI-->>DBX: Decision: RETRY<br/>Confidence: 85%
        DBX->>Lakebase: UPDATE status=NULL<br/>INCREMENT retry_count<br/>PREPEND '[AI: Retry Recommended]'
        Note over Lakebase: Table eligible for<br/>next sensor pickup
    else Manual Review Required
        AI-->>DBX: Decision: REVIEW<br/>Confidence: 70%
        DBX->>Lakebase: UPDATE status='Failed'<br/>PREPEND '[AI: Requires Review]'
        Note over Lakebase: Table flagged for<br/>manual intervention
    end

    DBX-->>Retry: Return analysis result
```

---

## 3. Incremental Load Details

Detailed sequence showing incremental value tracking and delta processing.

```mermaid
sequenceDiagram
    participant DL as DataLoader
    participant Lakebase as Lakebase DB
    participant Source as Source DB
    participant Spark as Spark Session
    participant UC as Unity Catalog

    Note over DL: load_strategy = 'incremental'<br/>incremental_column = 'modified_date'<br/>primary_key_columns = 'order_id'

    DL->>Lakebase: SELECT incremental_value<br/>FROM table_control<br/>WHERE control_key = ?
    Lakebase-->>DL: '2024-01-15 10:30:00'<br/>(or '1900-01-01' if first run)

    DL->>DL: build_query()<br/>Add WHERE clause based on db_type

    Note over DL: PostgreSQL:<br/>WHERE modified_date > ('2024-01-15'::timestamp + INTERVAL '1 second')<br/><br/>SQL Server:<br/>WHERE modified_date > CAST('2024-01-15' AS DATETIME2)<br/><br/>Oracle:<br/>WHERE CAST(modified_date AS DATE) > TO_DATE('2024-01-15', 'YYYY-MM-DD HH24:MI:SS')

    DL->>Source: spark.read.format("jdbc")<br/>.option("query", built_query)<br/>.load()
    Source-->>Spark: Return DataFrame (delta rows)

    DL->>DL: transform_column_names()<br/>(spaces → underscores)

    DL->>DL: Create staging table<br/>table_name__stg_{uuid}

    DL->>UC: overwrite_df_to_unity()<br/>Write to staging table
    UC-->>DL: Staging table created

    DL->>UC: DeltaTable.forName(target)
    DL->>UC: target.merge(source, merge_condition)<br/>.whenMatchedUpdateAll()<br/>.whenNotMatchedInsertAll()<br/>.execute()

    Note over DL,UC: merge_condition:<br/>target.order_id = source.order_id

    UC-->>DL: Merge complete

    DL->>UC: DROP TABLE staging_table
    UC-->>DL: Staging dropped

    DL->>UC: SELECT MAX(modified_date)<br/>FROM target_table
    UC-->>DL: '2024-01-20 14:45:30'

    DL->>DL: Stage update in update_list

    Note over DL: After all tables complete...

    DL->>Lakebase: Batch UPDATE table_control<br/>SET incremental_value = '2024-01-20 14:45:30'
```

---

## 4. Chunked Backfill Flow

Sequence for loading large tables in sequential chunks.

```mermaid
sequenceDiagram
    participant DL as DataLoader
    participant Lakebase as Lakebase DB
    participant Source as Source DB
    participant Spark as Spark Session
    participant UC as Unity Catalog

    Note over DL: load_strategy = 'chunked_backfill'<br/>incremental_column = 'id'<br/>incremental_type = 'integer'

    DL->>Source: SELECT MIN(id), MAX(id)<br/>FROM source_table
    Source-->>DL: min=1, max=10,000,000

    DL->>DL: _auto_calculate_chunks()<br/>Based on data type and range

    Note over DL: For integers: ~100k rows/chunk<br/>For timestamps: ~30 days/chunk<br/><br/>Result: 100 chunks<br/>[(1, 100000), (100001, 200000), ...]

    loop For i, (chunk_start, chunk_end) in chunks
        DL->>DL: build_chunk_query()<br/>WHERE id BETWEEN chunk_start AND chunk_end

        DL->>Source: Load chunk via JDBC
        Source-->>Spark: DataFrame for chunk

        alt First chunk (i=0)
            DL->>UC: write_chunk_to_unity(overwrite=True)
            Note over UC: Creates fresh table
        else Subsequent chunks
            DL->>UC: write_chunk_to_unity(overwrite=False)
            Note over UC: Appends to existing
        end

        UC-->>DL: Chunk written

        DL->>DL: Track current_max = chunk_end

        Note over DL: If error occurs here,<br/>save progress for resume
    end

    Note over DL: All chunks complete

    DL->>Lakebase: UPDATE table_control<br/>SET incremental_value = 10000000<br/>SET load_strategy = 'incremental'

    Note over DL: Future loads use<br/>incremental strategy
```

---

## 5. Timeout Handling

Sequence showing how stuck jobs are detected and handled.

```mermaid
sequenceDiagram
    participant LQM as longqueued_monitor
    participant LRM as longrunning_monitor
    participant Lakebase as Lakebase DB
    participant Dagster as Dagster Instance
    participant DBX as Databricks

    Note over LQM: Runs every 10 minutes<br/>Threshold: 60 minutes queued

    LQM->>Lakebase: SELECT * FROM table_control<br/>WHERE status='Queued'<br/>AND last_status_date_time < NOW() - 60 min
    Lakebase-->>LQM: Return stuck queued tables

    loop For each stuck queued table
        LQM->>Lakebase: UPDATE status='Failed'<br/>SET error_message='Cancelled - stuck in queue'
        LQM->>Lakebase: UPDATE historical_metadata<br/>SET load_status='Cancelled'
    end

    Note over LRM: Runs every 10 minutes<br/>Threshold: 180 minutes running

    LRM->>Lakebase: SELECT * FROM table_control<br/>WHERE status='In Progress'<br/>AND last_load_date_time < NOW() - 180 min
    Lakebase-->>LRM: Return stuck running tables

    loop For each stuck running table
        LRM->>Lakebase: Extract dagster_run_id from URL
        LRM->>Dagster: instance.run_coordinator.cancel_run(run_id)
        Dagster->>DBX: Terminate job

        LRM->>Lakebase: UPDATE status='Failed'<br/>SET error_message='Cancelled - exceeded timeout'
        LRM->>Lakebase: UPDATE historical_metadata<br/>SET load_status='Cancelled'
    end
```

---

## Related Documentation

- [Architecture Diagrams](02-architecture.md) - Component and flow diagrams
- [Dagster Orchestration](../reference/03-dagster-orchestration.md) - Sensor details
- [Load Strategies](../reference/05-load-strategies.md) - Strategy configuration
- [Troubleshooting](../reference/06-troubleshooting.md) - Debugging failed loads
