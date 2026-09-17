# dataloader-docs conventions

Documentation for the DataLoader system. Nothing here is generated; every page is written and
checked against the source by hand. `DakotaDataEng/dataloader-v1` runs the same app, loader and
schema, so it is the reference to check against.

## Rules

- **Check claims against code.** Every number, column name, sensor name and default in these docs
  is a fact about the code. Cite nothing you have not read. When code and docs disagree, the code
  wins and the doc gets fixed.
- **No em dashes or en dashes.** Use commas, colons, parentheses or a second sentence.
- **Short, plain sentences.** State the fact and move on. No filler openers, no reader
  hand-holding, none of the vocabulary that reads as machine-written.
- **Tables for reference data**, prose for explanation. A table of columns beats three paragraphs
  describing them.
- **Say why, not only what.** The useful half of this system is the reasoning: why loads are
  batched, why append_only gets no lookback, why a business date is a bad cursor. Keep it.
- **Screenshots are regenerated, not edited.** See below.

## Screenshots

`reference/images/dataloader-*.png` are captured from the Dakota demo instance of the same app
(`DakotaDataEng/dataloader-v1`), which runs the same `dl-app` code against its own Lakebase and
cluster. Demo data is Dakota's own, so nothing is redacted.

They are captured by a script, not by hand: run the app locally against `dataloader_test`, drive
it with Playwright in headless Edge, light theme, 1500px wide. Recapture a page after any UI
change rather than editing the image. The capture script lives with the session that produced it;
if it is gone, the settings above are enough to rebuild it.

## Layout

| Path | Holds |
|---|---|
| `README.md` | Repo landing page, points at the published site |
| `mkdocs.yml` | Site config and navigation. Material for MkDocs |
| `docs/index.md` | Site home, written for someone who has never seen DataLoader |
| `docs/reference/` | One file per subject, numbered in reading order |
| `docs/reference/images/` | Screenshots referenced by `07-control-manager-ui.md` |
| `docs/diagrams/` | Overview SVG, its generator, and the mermaid diagrams |
| `.github/workflows/docs.yml` | Builds with `--strict` on every PR, publishes from `main` |

## The site

Published to GitHub Pages at <https://dakotadataeng.github.io/dataloader-docs/>.

```bash
pip install -r requirements.txt
mkdocs serve            # preview at :8000
mkdocs build --strict   # what the workflow runs
```

Versions are pinned in `requirements.txt` so the local site matches the published one.

`--strict` turns a broken internal link into a failed build, and the workflow runs it on every pull
request. If you add a page, add it to `nav` in `mkdocs.yml`.
