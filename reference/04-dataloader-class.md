# DataLoader Class Reference

The DataLoader class (`dbx/functions/dataloader.py`) is the core data loading engine.

---

## Overview

| Property | Value |
|----------|-------|
| **Location** | `dbx/functions/dataloader.py` |
| **Language** | Python (PySpark) |
| **Threading** | ThreadPoolExecutor (default 12 workers) |
| **Supported DBs** | 6 database types |
| **Load Strategies** | 6 strategies |

---

## Constructor

```python
DataLoader(
    db_type: str,                    # Database type
    database_config: dict,           # Connection credentials
    full_load: str = "N",            # Force full reload
    control_schema: str = "control", # Lakebase schema
    load_strategy: str = None,       # Filter by strategy
    use_threading: bool = True,      # Enable parallel loading
    max_workers: int = 12,           # Thread pool size
    lakebase_config: dict = None     # Optional Lakebase override
)
```

**Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `db_type` | str | required | `mssql`, `oracle`, `postgresql`, `snowflake`, `snowflake_pem`, `clickhouse` |
| `database_config` | dict | required | Connection credentials (actual values, not Key Vault names) |
| `full_load` | str | `"N"` | `"Y"` forces full reload regardless of strategy |
| `control_schema` | str | `"control"` | Lakebase schema name |
| `load_strategy` | str | None | Filter tables by strategy |
| `use_threading` | bool | True | Enable parallel table processing |
| `max_workers` | int | 12 | Number of concurrent threads |

---

## Database Support

### Connection Strings

| Type | JDBC URL Format | Driver |
|------|-----------------|--------|
| `mssql` | `jdbc:sqlserver://{host}:{port};databaseName={database}` | `com.microsoft.sqlserver.jdbc.SQLServerDriver` |
| `oracle` | `jdbc:oracle:thin:@{host}:{port}/{service_name}` | `oracle.jdbc.driver.OracleDriver` |
| `postgresql` | `jdbc:postgresql://{host}:{port}/{database}` | `org.postgresql.Driver` |
| `clickhouse` | `jdbc:clickhouse://{host}:{port}/{database}` | `com.clickhouse.jdbc.ClickHouseDriver` |
| `snowflake` | Native connector | N/A |
| `snowflake_pem` | Native connector (key-pair) | N/A |

### database_config Structure

**SQL Server (mssql)**:
```python
{
    "host": "server.database.windows.net",
    "port": "1433",
    "database": "SalesDB",
    "user": "dataloader",
    "password": "secret",
    # Optional: Azure AD Service Principal
    "tenant_id": "xxx-xxx-xxx",  # Triggers SP auth if present
}
```

**Oracle**:
```python
{
    "host": "oracle.example.com",
    "port": "1521",
    "service_name": "ORCL",
    "user": "dataloader",
    "password": "secret"
}
```

**PostgreSQL**:
```python
{
    "host": "postgres.example.com",
    "port": "5432",
    "database": "analytics",
    "user": "dataloader",
    "password": "secret"
}
```

**Snowflake**:
```python
{
    "account": "orgname-accountname",
    "user": "dataloader",
    "password": "secret",
    "warehouse": "COMPUTE_WH",
    "database": "RAW",
    "schema": "PUBLIC"
}
```

**ClickHouse**:
```python
{
    "host": "clickhouse.example.com",
    "port": "8123",  # HTTP port
    "database": "default",
    "user": "dataloader",
    "password": "secret"
}
```

---

## Key Methods

### process_tables(table_list)

Main entry point for processing tables.

```python
def process_tables(self, table_list: list[dict]) -> None:
    """
    Process a list of tables, loading each from source to Unity Catalog.

    Args:
        table_list: List of table configuration dicts from table_control

    Each dict should contain:
        - source_table_catalog, source_table_schema, source_table_name
        - destination_table_catalog, destination_table_schema, destination_table_name
        - load_strategy, incremental_column, incremental_value
        - primary_key_cols (for merge strategies)
    """
```

**Execution Flow**:
1. Filter by `load_strategy` if specified
2. For each table:
   - If `use_threading`: Submit to ThreadPoolExecutor
   - Else: Process sequentially
3. After completion: Batch update Lakebase with incremental values

### load_table_from_source(table_dict, table_exists)

Load data from source database into Spark DataFrame.

```python
def load_table_from_source(self, table_dict: dict, table_exists: bool) -> DataFrame:
    """
    Query source database and return Spark DataFrame.

    Args:
        table_dict: Table configuration
        table_exists: Whether destination table exists

    Returns:
        Spark DataFrame with source data
    """
```

**Operations**:
1. Call `build_query()` to construct SQL
2. Execute JDBC/native read
3. Transform column names (spaces → underscores)
4. Return DataFrame

### build_query(table_dict)

Build source query based on load strategy.

```python
def build_query(self, table_dict: dict) -> str:
    """
    Build SQL query with strategy-specific filters.

    For incremental: WHERE col > last_value
    For rolling: WHERE col >= (today - N days)
    For full: SELECT * (no filter)
    """
```

**Query Patterns by Database**:

| Database | Timestamp Filter |
|----------|------------------|
| PostgreSQL | `WHERE col > ('2024-01-01'::timestamp + INTERVAL '1 second')` |
| SQL Server | `WHERE col > CAST('2024-01-01' AS DATETIME2)` |
| Oracle | `WHERE CAST(col AS DATE) > TO_DATE('2024-01-01', 'YYYY-MM-DD HH24:MI:SS')` |
| ClickHouse | `WHERE col > toDateTime('2024-01-01')` |

### write_table_to_unity(table_dict)

Orchestrate write to Unity Catalog.

```python
def write_table_to_unity(self, table_dict: dict) -> None:
    """
    Write DataFrame to Unity Catalog using appropriate strategy.

    Determines write mode based on:
    - Table existence
    - Load strategy
    - full_load override
    """
```

**Write Methods Called**:

| Scenario | Method |
|----------|--------|
| New table | `overwrite_df_to_unity()` |
| Full load | `overwrite_df_to_unity()` |
| Incremental | `merge_incremental_table()` |
| Append only | `append_only_df_to_unity()` |
| Rolling | `merge_rolling_table()` |
| Chunked backfill | `process_chunked_backfill()` |

### merge_incremental_table(df, table_dict)

Upsert data using Delta merge.

```python
def merge_incremental_table(self, df: DataFrame, table_dict: dict) -> None:
    """
    Merge DataFrame into existing table using primary keys.

    Steps:
    1. Create staging table
    2. Execute Delta merge (MATCHED UPDATE, NOT MATCHED INSERT)
    3. Drop staging table
    4. Update incremental_value
    """
```

**Merge Pattern**:
```sql
MERGE INTO target
USING staging
ON target.pk1 = staging.pk1 AND target.pk2 = staging.pk2
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
```

### merge_rolling_table(df, table_dict)

Merge with automatic deletion outside rolling window.

```python
def merge_rolling_table(self, df: DataFrame, table_dict: dict) -> None:
    """
    Merge with WHEN NOT MATCHED BY SOURCE DELETE for rolling window.

    Deletes records outside the rolling window automatically.
    """
```

**Merge Pattern**:
```sql
MERGE INTO target
USING staging
ON target.pk = staging.pk
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
WHEN NOT MATCHED BY SOURCE AND target.date >= (today - N days) THEN DELETE
```

### process_chunked_backfill(table_dict)

Process large tables in sequential chunks.

```python
def process_chunked_backfill(self, table_dict: dict) -> None:
    """
    Load large tables in chunks to avoid memory issues.

    Steps:
    1. Query source MIN/MAX of incremental column
    2. Calculate chunk boundaries
    3. Process chunks sequentially
    4. First chunk: overwrite, subsequent: append
    5. Update incremental_value at end
    """
```

**Chunk Sizing**:
- **Timestamps**: ~30 days per chunk
- **Integers**: ~100,000 rows per chunk (estimated)

---

## Threading Model

```python
if self.use_threading:
    with ThreadPoolExecutor(self.max_workers) as executor:
        executor.map(self.write_table_to_unity, filtered_table_list)
else:
    for table in filtered_table_list:
        self.write_table_to_unity(table)
```

**Thread Safety**:
- Each thread processes one complete table independently
- Shared lists (`update_list`, `errors_list`, `completed_list`) use CPython atomic append
- No explicit locks required

**Debugging**:
- Set `use_threading=False` to isolate single table issues
- Each thread captures its own exceptions

---

## Error Handling

### Per-Table Error Capture

```python
try:
    # Load and write operations
except Exception as e:
    error_info = {
        "table_name": table_name,
        "error_message": str(e),
        "error_type": type(e).__name__,
        "error_traceback": traceback.format_exc(),
        "timestamp": pendulum.now().format("YYYY-MM-DD HH:mm:ss ZZ"),
        "status": "failed"
    }
    self.errors_list.append(error_info)
```

### Results Access

```python
update_list, errors_list, completed_list = loader.get_results()
```

| List | Contents |
|------|----------|
| `update_list` | Incremental values to sync to Lakebase |
| `errors_list` | Failed table details with tracebacks |
| `completed_list` | Success records with row counts and timing |

---

## Special Features

### Geometry Column Handling

**SQL Server**:
```sql
SELECT col1, geometry::STAsText(col2) as col2_wkt FROM table
```

**Oracle**:
```sql
SELECT col1, SDO_UTIL.TO_WKTGEOMETRY(col2) as col2_wkt FROM table
```

### ClickHouse Type Inference

```python
# Custom schema override for type safety
df = spark.read.format("jdbc") \
    .option("customSchema", "col1 INT, col2 VARCHAR, col3 TIMESTAMP") \
    .load()
```

### Column Name Transformation

```python
def transform_column_names(self, df: DataFrame) -> DataFrame:
    """
    Replace spaces and slashes with underscores.
    'My Column' -> 'My_Column'
    'col/name' -> 'col_name'
    """
```

### Azure AD Service Principal Auth

```python
if "tenant_id" in database_config:
    token = _acquire_azure_sql_token(
        tenant_id=database_config["tenant_id"],
        client_id=database_config["user"],
        client_secret=database_config["password"]
    )
    connection_string["accessToken"] = token
```

---

## Logging and Observability

### log_event_data()

Returns comprehensive execution summary.

```python
{
    "execution": {
        "execution_id": uuid,
        "dagster_run_id": str,
        "dagster_run_url": str
    },
    "timing": {
        "start_time": str,
        "end_time": str,
        "duration_seconds": int
    },
    "configuration": {
        "source_type": str,
        "full_load": bool,
        "load_strategy_filter": str,
        "threading_enabled": bool,
        "max_workers": int
    },
    "results": {
        "total_tables_processed": int,
        "tables_succeeded": int,
        "tables_failed": int,
        "total_rows_processed": int
    },
    "tables": {
        "success": list[dict],
        "failure": list[dict]
    }
}
```

---

## File Location

```
dbx/
└── functions/
    └── dataloader.py    # ~40,000 lines
```

---

## Related Documentation

- [System Overview](01-system-overview.md) - High-level architecture
- [Load Strategies](05-load-strategies.md) - Strategy configuration
- [Sequence Diagrams](../diagrams/03-sequence-diagrams.md) - Execution flows
- [Troubleshooting](06-troubleshooting.md) - Debugging guide
