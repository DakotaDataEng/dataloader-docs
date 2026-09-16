# dataloader-docs conventions

Documentation for the DataLoader system. The code it describes lives in the Azure DevOps repo
`AnteroDataLakehouse` (local path `Antero/dbx-data`, branch `dev`). Nothing here is generated;
every page is written and checked against the code by hand.

## Rules

- **Check claims against code.** Every number, column name, sensor name and default in these docs
  is a fact about `dbx-data@dev`. Cite nothing you have not read. When code and docs disagree, the
  code wins and the doc gets fixed.
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
| `README.md` | Entry point and navigation by audience |
| `diagrams/` | System overview SVG and mermaid architecture and sequence diagrams |
| `reference/` | One file per subject, numbered in reading order |
| `reference/images/` | Screenshots referenced by `07-control-manager-ui.md` |
