# Common Tasks

Recipes for the things people actually do. Each one says what to click, what happens underneath, and
what to watch out for.

---

## Add one table

1. Open the database, **Add table**.
2. Fill in the source schema and table. Use **Preview rows** to prove you have the right one.
3. Set the destination catalog, schema and name. Destination names are limited to `[A-Za-z0-9_]`;
   the form rejects anything else and suggests a folded version.
4. Pick a strategy. Start with `full` unless the table is large.
5. Set the cron. It is shown in `America/Denver` and stored as UTC.
6. Leave **Active** off until you have reviewed it, then turn it on.

For anything other than `full`, press **Suggest columns**. It reads the source's columns, keys and
indexes and grades each one as an incremental cursor and as a partition column. Take its suggestion
rather than guessing: a column that looks like a date is often a business date that never moves when
a row is edited.

---

## Add many tables at once

Faster and more accurate than the form, because the defaults come from the source's own catalog.

1. On the table list, press **Crawl source catalog**. This submits a cluster run that reads every
   table's columns, primary key, indexes and row estimate. It is cheap: row counts are the engine's
   own estimates, never `COUNT(*)`.
2. Open **Source catalog**. Sort by Rows to see the big tables. Filter with **Hide already
   configured** and **Has a change column**.
3. Tick the tables you want, choose a shared strategy and schedule, and **Create configs**.

Each new row gets the table's primary key, a safe lower-case destination name, a partition column
when the table has a single numeric key and roughly 400k rows or more, and for incremental
strategies the best change column the crawl found.

Configurations are created inactive unless you tick **Active right away**, so you can review them on
the table list first.

Re-crawl after the source changes. The cache is only ever as fresh as the last crawl.

---

## Add a source database

1. **Add database**. Pick the type; the secret fields change with it.
2. Choose a secret layout: one Key Vault secret per field, or one secret holding a JSON bundle.
3. Enter the **names** of the Key Vault secrets, not the values. Use **Set value** to write a value
   into the vault from here, or manage it in the Key Vault explorer.
4. Press **Test connection** before saving. It connects on the dataloader cluster with exactly the
   secrets you configured and reports which ones resolved.

Test connection and the other cluster-backed buttons are disabled on PROD.

---

## Reload a table from scratch

**Small or medium table**: tick **Full Load Override** and save, or use the Full toggle on the table
list. The next run reads the whole source and replaces the destination, then the flag clears itself.

A full load is one Delta overwrite in a single atomic commit. There is no truncate and no window
where readers see an empty or half-written table. Readers on the old version finish against it; the
next query sees the new one.

The override also bypasses the strategy's window, so an incremental table genuinely reads
everything. Without that, the overwrite would leave the table holding only the window.

**Very large table**: do not use Full Load Override. Set the strategy to `chunked_backfill` instead,
see below.

---

## Backfill a very large table

Set the strategy to `chunked_backfill`, clear **Incremental Value**, and let it run. There is no
need to drop the destination.

What happens: the loader reads the source's MIN and MAX, cuts the range into chunks sized by row
count, and loads them in order. The first chunk of a fresh start overwrites the destination; the
rest append. Every finished chunk records its upper bound, so a run that dies has a resume point.

If it is interrupted, just run it again. It continues from the mark, first deleting any destination
rows at or above it, which can only have come from a chunk that appended without recording itself.

When the last chunk lands, the loader switches the row to `incremental` and sets the cursor, and
writes an audit entry with source `backfill_complete`.

Two things to watch:

- **The table is partial while it runs.** Chunk one replaces the whole table, so anyone querying it
  sees only the chunks loaded so far. If that is not acceptable, load into a new destination name
  and rename afterwards.
- **Give it a numeric partition column** if the table has one. The loader then cuts chunks on an
  indexed key range rather than on a usually unindexed timestamp, which is far faster on the source.

Chunk sizing is automatic. `DATALOADER_ROWS_PER_CHUNK` tunes it; `num_partitions` does not.

---

## A source column was added

Usually nothing to do. Every write path evolves the schema: merges use Delta auto-merge, appends use
`mergeSchema`, overwrites use `overwriteSchema`. The next incremental run adds the column and fills
it for the rows in that window.

Rerun from scratch only when you need the column populated for **history**. Rows loaded before the
column existed stay NULL, because incremental only touches rows the source changed since the cursor.
If the source backfilled the column into old rows without touching their modified date, those rows
will never come through on their own.

Cases evolution does not cover:

| Change | What to do |
|---|---|
| Column added, only new rows matter | Nothing |
| Column added, history must be filled | Reload, see above |
| Column type changed | Reload; a merge cannot change a column's type |
| Column renamed | Reload; a merge sees a new column and an old one that goes quiet |
| Column dropped at source | Nothing breaks. The destination keeps it, NULL for new rows. It disappears on a reload |

---

## A load failed

1. Open **Operations** or the Failed tab of the status view. Expand the row to read the error.
2. Follow the Databricks link for the real stack trace. The control table message is a summary.
3. Fix the cause, then **Reset** the table. Reset clears the status so the next sensor tick picks it
   up. It does not start a run itself.

The failed monitor also sends failures to an LLM for classification, and will either reset the table
for retry on its own or flag it for review, up to three retries.

If a whole batch failed, expect several tables from one database to fail together. The run that
carried them is in the Dagster link on any of them.

---

## A table is stuck In Progress

It usually means the run that owned it died without reporting.

The reconcile monitor handles this on its own within about 20 minutes: it marks the row Failed once
the recorded Dagster run is gone or finished, and closes the history row. If you need it sooner,
press **Reset**.

If many tables are stuck at once, check that the Dagster sensors are actually running.

---

## Nothing is running at all

In order:

1. **Are the sensors on?** Four of them ship stopped, and they are also the four that matter most:
   master, longqueued, longrunning, failed. A fresh deployment or a restored deployment can leave
   them off. Check the Dagster UI.
2. **Is the table due?** A row only qualifies when `is_active` is true and it is past
   `next_load_date_time`. On the table list, the Overdue chip shows rows that are due and not being
   picked up.
3. **Is the status blocking it?** The view only re-qualifies a row whose last status is `Succeeded`
   or NULL. A row sitting at `Failed` will not run again until it is reset.
4. **Is a backfill hogging the database?** Only one chunked backfill runs per source database at a
   time. The sensor log names what it deferred.

---

## Promote configuration to production

1. Open **Diff and promote** and pick the database.
2. Use the **Needs promotion** chip. Expand rows to see exactly which fields differ.
3. Press **Preview** before promoting. It lists the inserts, updates and renames, the database
   configuration it would copy, and any Key Vault secrets missing from the production vault, with a
   checkbox to copy each one.
4. Promote. It runs in one transaction.

Promotion moves configuration, never data. The production tables load on their own schedule
afterwards.

Use `[` and `]` to move between databases without touching the mouse. Press `?` for the rest.

---

## Turn on delete tracking

Set **Mark Deletes** on the table and choose a mode. It needs a primary key.

| Mode | Behavior |
|---|---|
| Soft (default) | Sets `is_delete` and `deleted_at` on rows that vanished from the source, keeps the row. A row that comes back is restored |
| Hard | Removes the row from the destination |

Anything unrecognised resolves to soft, deliberately: a typo must never escalate into deleting rows.

Reconciliation compares source and destination keys. It does not run on every load; by default it
runs when the last check is more than 24 hours old. If the source key query comes back empty while
the destination holds rows, the check is skipped rather than treated as a full-table delete.

The bookkeeping columns are added to an existing table on demand, so turning this on does not
require a rebuild.

---

## Check a table has not fallen behind

Row counts are compared after every load with nothing to turn on. The edit rail shows them as
**Row counts**: in step, or both numbers and how far apart. They see net drift only, so equal
numbers are not proof.

For the exact answer, set **Check for Missed Rows** on the table. It compares every source primary
key with the destination's and reports rows that no incremental run can see, because their
incremental column never moved above the cursor. The rail shows the result as **Source vs
destination** and the table list marks the table when rows are missing.

It needs a primary key, and what it costs depends on the table:

| Mark Deletes | Cost of the drift check |
|---|---|
| On | Free. Those keys are already being read, this is one more join over them |
| Off | A full read of the source primary keys, on its own daily schedule |

The form says which case you are in when you tick the box.

**Mark Deletes does not turn this on.** The two answers come from one read of the source keys,
but each is only produced when its own setting asks for it. Mark Deletes finds rows that vanished
from the source. Check for Missed Rows finds rows that never arrived. Tick both if you want both.

**It is not available on a table loaded by a custom SQL query.** The key read covers the whole
source table, so every row the query filters out would be reported as missing. The form says so
and the loader skips that half.

Nothing is loaded automatically. Missing rows are reported, and the repair is a Force Full Reload
once you have fixed whatever let them through, usually a cursor pointing at a business date. Fixing
the cursor alone does not go back for the rows already missed.

---

## Speed up a slow incremental table

In rough order of payoff:

1. **Give it a partition column.** A windowed read with a numeric partition column is read over
   parallel JDBC connections. Without one, the whole window comes over a single connection into a
   single Spark partition, which is also the usual cause of a worker running out of memory.
2. **Check the cursor is indexed.** Suggest columns says so directly. An unindexed cursor means the
   source scans the table on every run.
3. **Lower the lookback.** Every incremental run re-reads a window below the cursor so late commits
   are not lost. The default is 12 hours. A table that runs every 15 minutes re-reads 12 hours of
   rows 96 times a day; set **Lookback hours** to 1 or 2 there.
4. **Check the delete reconciliation.** If Mark Deletes is on, the loader pulls the source's primary
   keys periodically. `DATALOADER_DELETE_CHECK_HOURS` controls how often.
5. **On SQL Server, prefer a rowversion.** It is the engine's own change counter, always moves on a
   write, and is usually indexed. The loader casts it to bigint automatically.

---

## Related documentation

- [Control Manager UI](07-control-manager-ui.md), where these buttons live
- [Load Strategies](05-load-strategies.md), what each strategy does
- [Troubleshooting](06-troubleshooting.md), diagnosis and queries
- [Dagster Orchestration](03-dagster-orchestration.md), why a run holds several tables
