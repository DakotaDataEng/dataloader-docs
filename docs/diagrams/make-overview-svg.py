"""Generate diagrams/01-system-overview.svg for dataloader-docs."""
import pathlib

W, H = 1240, 730
OUT = pathlib.Path(r"C:\Users\Jordan\Documents\dataloader-docs\diagrams\01-system-overview.svg")

INK = "#1B252C"
MUTED = "#5F6E78"
RULE = "#CBD5DB"
BG = "#FFFFFF"
BAND = "#F4F6F8"
BLUE = "#466A7D"
BLUE_BG = "#E8EFF3"
AMBER = "#B4640E"
AMBER_BG = "#FBEEDB"
GREEN = "#2F7A4E"
GREEN_BG = "#E4F2E9"
PURPLE = "#5B4B8A"
PURPLE_BG = "#ECE8F5"

p = []
add = p.append

add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
    f'font-family="Segoe UI, Helvetica, Arial, sans-serif">')
add(f'<rect width="{W}" height="{H}" fill="{BG}"/>')
add('<defs>')
add(f'<marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
    f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{MUTED}"/></marker>')
add(f'<marker id="ad" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
    f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{BLUE}"/></marker>')
add(f'<marker id="ag" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
    f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{GREEN}"/></marker>')
add(f'<marker id="ap" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
    f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{PURPLE}"/></marker>')
add('</defs>')


def text(x, y, s, size=13, fill=INK, weight="normal", anchor="start", mono=False):
    fam = ' font-family="Consolas, monospace"' if mono else ""
    add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" font-weight="{weight}" '
        f'text-anchor="{anchor}"{fam}>{s}</text>')


def box(x, y, w, h, fill, stroke, rx=6, sw=1):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="{sw}"/>')


def arrow(x1, y1, x2, y2, color=MUTED, dash=None, marker="a"):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    add(f'<path d="M{x1},{y1} L{x2},{y2}" stroke="{color}" stroke-width="1.6" fill="none" '
        f'marker-end="url(#{marker})"{d}/>')


# ---------------------------------------------------------------- header
text(40, 46, "DataLoader", 26, INK, "600")
text(40, 70, "Source databases to Unity Catalog bronze, driven by configuration in Postgres", 14, MUTED)

# column headers
cols = [(40, "SOURCES"), (300, "CONTROL"), (600, "ORCHESTRATION"), (900, "EXECUTION")]
for x, label in cols:
    text(x, 108, label, 11, MUTED, "600")
text(1200, 108, "DESTINATION", 11, MUTED, "600", anchor="end")

# ---------------------------------------------------------------- sources
SRC_Y = 124
sources = ["SQL Server", "Oracle", "PostgreSQL", "Snowflake", "Snowflake PEM", "ClickHouse", "S3 Iceberg"]
box(40, SRC_Y, 200, 236, BAND, RULE)
for i, s in enumerate(sources):
    y = SRC_Y + 30 + i * 30
    text(60, y, s, 13, INK)
text(60, SRC_Y + 30 + 7 * 30 + 4, "JDBC, native, or Spark", 11, MUTED)

# ---------------------------------------------------------------- lakebase
LB_X, LB_Y, LB_W, LB_H = 300, SRC_Y, 240, 236
box(LB_X, LB_Y, LB_W, LB_H, BLUE_BG, BLUE)
text(LB_X + 20, LB_Y + 28, "Lakebase", 15, BLUE, "600")
text(LB_X + 20, LB_Y + 46, "Postgres control database", 11, MUTED)
tables = [
    "table_control",
    "table_control_dbconfig",
    "dataloader_control_vw",
    "historical_metadata",
    "table_control_history",
    "source_catalog",
    "dagster_reload_request",
]
for i, t in enumerate(tables):
    text(LB_X + 20, LB_Y + 74 + i * 22, t, 11.5, INK, mono=True)
text(LB_X + 20, LB_Y + 74 + 7 * 22 + 2, "dataloader / dataloader_test", 11, MUTED)

# ---------------------------------------------------------------- dagster
DG_X, DG_Y, DG_W, DG_H = 600, SRC_Y, 250, 236
box(DG_X, DG_Y, DG_W, DG_H, AMBER_BG, AMBER)
text(DG_X + 20, DG_Y + 28, "Dagster", 15, AMBER, "600")
text(DG_X + 20, DG_Y + 46, "8 sensors, 1 schedule", 11, MUTED)
sensors = [
    ("master_sensor", "60s"),
    ("longqueued_monitor", "10m"),
    ("longrunning_monitor", "10m"),
    ("failed_monitor", "15m"),
    ("reconcile_monitor", "10m"),
    ("run_canceled / run_failed", "event"),
    ("dagster_reload_sensor", "5m"),
]
for i, (s, iv) in enumerate(sensors):
    y = DG_Y + 74 + i * 22
    text(DG_X + 20, y, s, 11.5, INK, mono=True)
    text(DG_X + DG_W - 20, y, iv, 10.5, MUTED, anchor="end")
text(DG_X + 20, DG_Y + 74 + 7 * 22 + 2, "batches the work", 11, MUTED)

# ---------------------------------------------------------------- databricks
DX, DY, DW, DH = 900, SRC_Y, 300, 236
box(DX, DY, DW, DH, GREEN_BG, GREEN)
text(DX + 20, DY + 28, "Databricks", 15, GREEN, "600")
text(DX + 20, DY + 46, "one job run per batch", 11, MUTED)
text(DX + 20, DY + 78, "dataloader_pipe.py", 11.5, INK, mono=True)
text(DX + 20, DY + 100, "one DataLoader per batch", 12, INK)
text(DX + 20, DY + 122, "up to 12 table threads", 12, INK)
text(DX + 20, DY + 152, "Strategies", 11, MUTED, "600")
text(DX + 20, DY + 172, "full  incremental  append_only", 11.5, INK, mono=True)
text(DX + 20, DY + 190, "rolling  check_and_load", 11.5, INK, mono=True)
text(DX + 20, DY + 208, "chunked_backfill", 11.5, INK, mono=True)

# ---------------------------------------------------------------- unity catalog
UC_Y = 400
box(900, UC_Y, 300, 96, BAND, RULE)
text(920, UC_Y + 30, "Unity Catalog", 15, INK, "600")
text(920, UC_Y + 52, "bronze layer, Delta tables", 12, MUTED)
text(920, UC_Y + 76, "deletion vectors, optimized writes,", 11, MUTED)
text(920, UC_Y + 90, "clustering on the merge key", 11, MUTED)

# ---------------------------------------------------------------- key vault
KV_Y = 400
box(40, KV_Y, 200, 96, BAND, RULE)
text(60, KV_Y + 30, "Azure Key Vault", 14, INK, "600")
text(60, KV_Y + 52, "secret values", 12, MUTED)
text(60, KV_Y + 76, "the control database stores", 11, MUTED)
text(60, KV_Y + 90, "names only", 11, MUTED)

# ---------------------------------------------------------------- dl-app
APP_X, APP_Y, APP_W, APP_H = 300, 560, 550, 104
box(APP_X, APP_Y, APP_W, APP_H, PURPLE_BG, PURPLE)
text(APP_X + 20, APP_Y + 28, "dl-app", 15, PURPLE, "600")
text(APP_X + 90, APP_Y + 28, "Databricks App, how the system is operated", 12, MUTED)
line1 = "Databases   Operations   Runs   Trends   Audit History   Key Vault"
text(APP_X + 20, APP_Y + 54, line1, 11.5, INK, mono=True)
line2 = "Source catalog crawl, bulk create, promote to prod, secret management"
text(APP_X + 20, APP_Y + 76, line2, 12, INK)
text(APP_X + 20, APP_Y + 94, "Test connection, Suggest columns, Preview rows run on the cluster", 11, MUTED)

# ---------------------------------------------------------------- flow arrows
mid = SRC_Y + 118

# control path: lakebase -> dagster -> databricks, and per-table status back
arrow(LB_X + LB_W, mid, DG_X - 4, mid)
arrow(DG_X + DG_W, mid, DX - 4, mid)
text(570, mid - 10, "due tables", 10, MUTED, anchor="middle")
text(875, mid - 10, "batch", 10, MUTED, anchor="middle")

add(f'<path d="M{DX - 4},{mid + 66} L{DG_X + DG_W + 4},{mid + 66}" stroke="{MUTED}" '
    f'stroke-width="1.6" fill="none" stroke-dasharray="4 3" marker-end="url(#a)"/>')
text(875, mid + 82, "per-table status", 10, MUTED, anchor="middle")

# data path: sources -> databricks, routed under the middle columns
add(f'<path d="M240,340 L262,340 L262,388 L880,388 L880,340 L{DX - 6},340" stroke="{GREEN}" '
    f'stroke-width="1.6" fill="none" marker-end="url(#ag)"/>')
text(500, 374, "rows read over JDBC, native, or Spark", 10, GREEN, anchor="middle")

# databricks -> unity catalog
arrow(DX + 150, DY + DH, DX + 150, UC_Y - 4, GREEN, marker="ag")
text(DX + 160, UC_Y - 12, "write Delta", 10, GREEN)

# key vault -> databricks, routed clear of unity catalog
add(f'<path d="M240,470 L870,470 L870,300 L{DX - 6},300" stroke="{MUTED}" stroke-width="1.4" '
    f'fill="none" stroke-dasharray="4 3" marker-end="url(#a)" opacity="0.75"/>')
text(556, 464, "credentials at run time", 10, MUTED, anchor="middle")

# dl-app -> lakebase
arrow(APP_X + 110, APP_Y, APP_X + 110, LB_Y + LB_H + 6, BLUE, marker="ad")
text(APP_X + 120, APP_Y - 14, "read and write configuration", 10, BLUE)

# dl-app -> databricks, routed around the right edge
add(f'<path d="M{APP_X + APP_W},{APP_Y + 52} L1218,{APP_Y + 52} L1218,300 L{DX + DW + 6},300" '
    f'stroke="{PURPLE}" stroke-width="1.4" fill="none" stroke-dasharray="4 3" '
    f'marker-end="url(#ap)" opacity="0.8"/>')
text(1210, APP_Y + 40, "one-off cluster runs", 10, PURPLE, anchor="end")

# ---------------------------------------------------------------- footnote
text(40, H - 18, "One Dagster run loads up to 12 tables that share a source database. "
                 "Outcomes are recorded per table as each finishes.", 12, MUTED)

add('</svg>')
OUT.write_text("\n".join(p), encoding="utf-8")
print("wrote", OUT, OUT.stat().st_size, "bytes")
