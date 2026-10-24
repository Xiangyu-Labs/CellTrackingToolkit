# Changelog

Newest changes first. Add new entries under "Unreleased"; the release
workflow replaces that heading with the version number and release time.

## Unreleased

## 2.0.17 — 2026-10-24 17:42 (UTC+8)

- Fixed the Updates tab showing no changes on the server version.
- Added an "Edit figures" panel to the analysis result page. Group names,
  group order and colours, trajectory-type colours, any figure text, font
  size, resolution and output formats (PNG, TIFF, PDF, SVG) can be changed
  and the figures re-rendered without changing code. Re-rendering creates a
  new result from the same datasets and parameters, so the data and the
  statistics stay the same, and the previous result is kept.
- Added composite figures: panels from different figures can be arranged in
  columns with panel letters, with presets matching the manuscript layouts.
  Styles can be exported and imported as JSON.
- PDF and SVG exports keep their text editable.
- Figures can be shown in µm and minutes. Set "µm per pixel" and "Minutes
  per frame" under "Units" in "Edit figures": lengths, speeds and areas are
  converted, time axes show hours, and directionality, angles, trajectory
  classes and p-values stay the same. Without these values nothing changes.
- Composite figures are listed on the result page under their own name, such
  as "Figure 1".

## 2.0.16 — 2026-10-13 19:24 (UTC+8)

- Added an Updates tab that lists what changed in each version.

## 2.0.15 — 2026-10-13 19:08 (UTC+8)

- Added a Docker image for running the application on a server.
- Added an optional access token for server deployments. When it is set,
  every visitor who has not opened the link with the correct token is
  refused.

## 2.0.14 — 2026-10-13 18:51 (UTC+8)

- Cell appearance figure: the three panels now show the mean per sample with
  an SEM band instead of totals summed over each group, so a group with more
  samples no longer looks like it has more cells.

## 2.0.13 — 2026-10-13 18:30 (UTC+8)

- Cell appearance figure: the panels stop at the last frame where a new track
  can still be counted, removing the drop to zero at the end of the movie.
- A track's first frame no longer counts as a step of zero distance and zero
  speed, which had pulled the frame-1 averages down to zero.
- Composition legend moved above the bars so it no longer covers them.

## 2.0.12 — 2026-10-13 17:32 (UTC+8)

- Restored the dataset-level figures approved earlier: parameter
  distributions as box plots of dataset means, the step turning-angle
  distribution, and MSD averaged within each dataset first.
- The New tracks panel starts after the first frame, so cells already present
  at the start no longer appear as a spike.

## 2.0.11 — 2026-09-21 21:34 (UTC+8)

- All trajectory panels in a figure share one axis scale again, stated in the
  panel titles.
- Significance brackets no longer run into neighbouring panels, and both
  halves of the paired parameter figure are 3 x 3 grids with matching metrics.
- Detections seen in fewer than 5 frames are left out of the cell appearance
  figure.
- Figure numbers no longer change with the selection of figures.
- Duplicate frames are detected, skipped and flagged in the dataset list. No
  file is deleted.
- Startup now repairs a project folder copied from another type of computer
  instead of reporting a misleading network error.

## 2.0.10 — 2026-08-28 23:37 (UTC+8)

- Added paper-ready analysis figures and composite panels. The default figure
  set is now cell appearance, long-track temporal trends, trajectory
  classification, turning-angle distribution, long-track MSD, representative
  trajectories, group trajectories, and a 3 x 3 grid of parameter
  distributions.
- Directionality is bounded to [0, 1], and displacement-angle plots show
  percentages on one shared scale.

## 2.0.9 — 2026-08-28 17:42 (UTC+8)

- Fixed the browser showing outdated pages after an update.

## 2.0.8 — 2026-08-28 17:33 (UTC+8)

- Added downloads for the current result frame and for all results of a
  dataset as an archive.

## 2.0.7 — 2026-08-28 16:20 (UTC+8)

- Fixed navigation between the Process and Analysis tabs and the page layout.

## 2.0.6 — 2026-08-28 13:28 (UTC+8)

- Segmentation and tracking now share one Process page, with search, filters
  by stage and task status, and batch selection.

## 2.0.5 — 2026-08-22 16:04 (UTC+8)

- Statistics now treat each dataset as one replicate: Mann-Whitney U for two
  groups, Kruskal-Wallis with Holm-adjusted pairwise tests for three or more,
  with effect sizes in the export.
- MSD curves show a 95% confidence interval and stop when fewer than half of
  a group's datasets contribute.

## 2.0.4 — 2026-08-19 19:29 (UTC+8)

- Fixed Windows startup failing while downloading tools.

## 2.0.3 — 2026-08-13 17:48 (UTC+8)

- Simplified the setup and usage guide.
- New versions are published automatically after the tests pass.

## 2.0.2 — 2026-08-13 16:32 (UTC+8)

- Releases include an empty Datasets folder to put image sequences in.

## 2.0.1 — 2026-08-13 15:47 (UTC+8)

- First release: segmentation, tracking and group comparison in the browser.
- One-click launchers for macOS, Windows and Linux.
- Verified segmentation model download and safe automatic updates.
