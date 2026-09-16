# DataLoader Documentation

DataLoader moves data from source databases into the Databricks Unity Catalog bronze layer. What
loads, how, and on what schedule is configuration in a Postgres database, not code. Adding a table
is a row, not a deployment.

---

## Start here

| If you are | Read |
|---|---|
| New to the system | [System Overview](reference/01-system-overview.md) |
| Running it day to day | [Common Tasks](reference/08-common-tasks.md) |
| Fixing something broken | [Troubleshooting](reference/06-troubleshooting.md) |
| Using the web app | [Control Manager UI](reference/07-control-manager-ui.md) |
| Reviewing the design | [Architecture Diagrams](diagrams/02-architecture.md) |
| Changing the loader | [DataLoader Class](reference/04-dataloader-class.md) |

---

## The system in one picture

```
Source Databases          Control            Orchestration         Execution           Destination
----------------          -------            -------------         ---------           -----------
SQL Server    ─┐
Oracle        ─┤                                                                       Unity Catalog
PostgreSQL    ─┼─►   Lakebase (Postgres)  ◄──►   Dagster    ──►   Databricks   ──►     bronze layer
Snowflake     ─┤     what, how, when           8 sensors         one job per          Delta tables
ClickHouse    ─┤     and what happened         batches the       batch of up
S3 Iceberg    ─┘                               work              to 12 tables
                            ▲
                            │
                         dl-app
                    (Databricks App)
```

Three things surprise people who knew the older design:

1. **One run loads up to twelve tables**, not one. The sensor groups the tables that are due by
   source database. Outcomes are recorded per table as each finishes.
2. **dl-app is not a local dev tool.** It is a deployed Databricks App and it is how the system is
   operated.
3. **A row that failed will not run again on its own.** The view the sensor reads only re-qualifies
   a row whose last status is `Succeeded` or NULL.

---

## Reference

| File | Covers |
|---|---|
| [01-system-overview.md](reference/01-system-overview.md) | What it is, key concepts, end to end flow |
| [02-lakebase-control-database.md](reference/02-lakebase-control-database.md) | Every control table, the view the sensor reads, diagnostic queries |
| [03-dagster-orchestration.md](reference/03-dagster-orchestration.md) | Batching, all eight sensors, assets, failure attribution |
| [04-dataloader-class.md](reference/04-dataloader-class.md) | The loader and the modules around it |
| [05-load-strategies.md](reference/05-load-strategies.md) | The six strategies, cursors, deletes, backfills |
| [06-troubleshooting.md](reference/06-troubleshooting.md) | Symptoms, causes, queries |
| [07-control-manager-ui.md](reference/07-control-manager-ui.md) | The web app, screen by screen |
| [08-common-tasks.md](reference/08-common-tasks.md) | Recipes: add tables, reload, backfill, promote |

## Diagrams

| File | Covers |
|---|---|
| [01-system-overview.svg](diagrams/01-system-overview.svg) | One page visual of the whole system |
| [02-architecture.md](diagrams/02-architecture.md) | Components, data flow, sensors, state machines |
| [03-sequence-diagrams.md](diagrams/03-sequence-diagrams.md) | Batch load, batch failure, incremental, backfill, timeouts |

---

## Components

**Lakebase (control database).** Postgres. `table_control` holds one row per table.
`table_control_dbconfig` holds one row per source database, storing Key Vault secret **names**, never
values. `historical_metadata` records every run, `table_control_history` every configuration change,
and `source_catalog` caches what a source database contains. Two databases: `dataloader` for
production and `dataloader_test` for pre-production.

**Dagster (orchestration).** Self-hosted. Eight sensors and one schedule. The master sensor finds
tables that are due, gates chunked backfills to one per database, and starts one run per batch. Four
sensors ship stopped and have to be enabled after a deployment.

**DataLoader (execution).** A Spark class plus focused modules for cursors, backfill resume,
identifier quoting, source connection settings, catalog crawling and column advice. One instance
loads a whole batch on parallel threads.

**dl-app (interface).** Flask, deployed as a Databricks App. Configure databases and tables, watch
loads on the Operations and Runs pages, crawl a source catalog and create tables in bulk, promote
configuration to production, and manage Key Vault secrets.

---

## Conventions

See [CLAUDE.md](CLAUDE.md). The short version: every claim here is checked against
`AnteroDataLakehouse@dev`, and when the code and the docs disagree, the docs are wrong.
