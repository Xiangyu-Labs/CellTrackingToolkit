# Cell Tracking Studio

## Setup

1. Download the latest application ZIP from GitHub Releases.
2. Extract the ZIP to a permanent folder.
3. Keep an internet connection available during the first launch. The required
   environment and segmentation model are downloaded automatically.

## Start the application

### macOS

Double-click `start.command`.

If macOS displays **"start.command Not Opened"**:

1. Click **Done**. Do not click **Move to Trash**.
2. Right-click `start.command`, select **Open**, and confirm **Open**.
3. If necessary, go to **System Settings → Privacy & Security** and click
   **Open Anyway**.

If it is still blocked, open Terminal, type the following command, and add one
space after it:

```sh
xattr -dr com.apple.quarantine
```

Do not press Return yet. Drag the **entire extracted Cell Tracking Studio
folder** from Finder into the Terminal window, then press Return. For example:

```sh
xattr -dr com.apple.quarantine "/path/to/extracted/CellTracking-Studio"
```

Double-click `start.command` again.

### Windows

Double-click `start.bat`.

### Linux

Double-click `start-linux.desktop` and select **Allow Launching** if prompted,
or run:

```sh
./start.sh
```

### Server (Docker)

The `Dockerfile` builds a server image without the automatic updater. Mount
`Datasets/`, `workspace/`, and `models/` at `/app/Datasets`, `/app/workspace`,
and `/app/models`, and publish port 8000.

When the server is reachable from the internet, set `CELLTRACK_ACCESS_TOKEN`
to a long random value. Every request is then refused with `403` unless it
carries the token: open `https://<host>/?token=<value>` once, and the browser
keeps a cookie for 30 days.

## Add datasets

Put each image sequence in its own folder inside `Datasets/`:

```text
Datasets/
├── Control/
│   ├── Sample_01/
│   │   ├── frame_001.tif
│   │   └── frame_002.tif
│   └── Sample_02/
│       ├── frame_001.tif
│       └── frame_002.tif
└── Treatment/
    └── Sample_03/
        ├── frame_001.tif
        └── frame_002.tif
```

Folders such as `Control` and `Treatment` are optional groups. A folder that
directly contains images is treated as one dataset.

Supported formats: `.bmp`, `.jpeg`, `.jpg`, `.png`, `.tif`, and `.tiff`.

## Results and logs

- Processing results: `workspace/results/`
- Group analysis: `workspace/analysis/`
- Troubleshooting logs: `workspace/logs/`

Application updates do not replace or delete `Datasets/`, `models/`, or
`workspace/`.

## Adjust figures without changing code

Open an analysis result and expand **Edit figures**. Everything there changes
how the figures look, never what they show:

- **Groups**: rename or reorder groups and pick their colours. Every group
  stays in the comparison. Reordering only changes which group is listed
  first in the statistics export: the p-values stay the same, the
  Mann-Whitney U is reported for the other group and effect sizes change
  sign.
- **Trajectory type colours** for Random, Mixed and Directed, and optionally
  one colour for every final-displacement-angle (polar) plot. By default each
  polar plot takes its group's colour; a single colour (grey suggested) keeps
  green and orange meaning trajectory types only, next to the trajectory
  panels.
- **Print size**: the width the figure will be printed at (85, 120 or 180 mm,
  or another width), the smallest text size in points, and a page height
  limit. Every figure is then laid out at that width: text prints at journal
  sizes (titles one or two points larger), long titles wrap, crowded tick
  labels turn, tick numbers repeat only on the outer panels of shared-scale
  grids, two-group representative trajectories stack the groups, and PNG and
  TIFF files carry the chosen DPI at exactly that width. A figure taller than
  the limit is shortened while every plot stays at least 12 mm tall;
  otherwise it keeps its height and the result page shows a note above it,
  as it does when text overlaps. Significance stars get the headroom they
  need in shorter panels. The font size multiplier is not used while a print width is set.
- **Output**: resolution (DPI), font size multiplier, extra formats (TIFF,
  PDF, SVG; text stays editable in PDF and SVG), hiding figure titles, and
  short dataset names on representative tracks.
- **Wording**: every title, axis label, tick label and legend entry is
  listed. Type a replacement, or tick *Hide* to remove it. Matching is
  exact, by whole label or by single line, so `Glutaraldehyde` also renames
  the first line of `Glutaraldehyde` / `n=10`.
- **Units**: µm per pixel and minutes per frame. This is the one setting
  that changes numbers, and only by a scale: lengths, speeds and areas are
  converted and time axes show hours since the first frame, while
  directionality, roundness, angles, the trajectory classes and every p-value
  stay the same. Axis labels change with the units, so text replacements
  written for the pixel labels need to be written again. Leave both empty to
  keep pixels and frames.
- **Composite figures**: put any panels into columns (read top to bottom,
  lettered A, B, C down each column in turn). Presets reproduce the
  manuscript layouts. Each composite is saved as
  `composite_NN_<name>.png` next to the standard figures.

**Re-render figures** creates a new result from the same datasets and
parameters (with the units above) and opens it; the result you started from is kept, and the new
one links back to it. **Export style** saves the settings as JSON, so the
same look can be applied to another analysis with **Import style**.

The standard figures with the default style are pixel-for-pixel the same as
before this panel existed. `tests/test_figure_style.py` pins the plotted
data of every figure, so any code change that would alter what a figure
shows fails the tests.

## Analysis methods

Every figure type is exported by default: cell appearance, temporal trends
(long tracks and all tracks), trajectory classification, step turning-angle
distribution, MSD (long tracks, all tracks, and the standalone MSD summary),
representative trajectories, group trajectory analysis, and a 3 x 3 grid of
dataset-level parameter distributions. Figure filenames are numbered in a fixed
canonical order, so a given figure keeps its `figure_NN_` prefix no matter
which other figures are selected. New analyses also include paper-ready
composite PNGs whenever their component figures are selected; existing results
in `workspace/analysis/` are left unchanged.

The temporal grid and the parameter-distribution grid are paired: panel *i* on
the left of `paper_02` shows the per-frame trend for the same quantity that
panel *i* on the right summarises per track. Both grids are 3 x 3.

Each dataset is treated as one independent experimental replicate. Tracks
within a dataset are technical observations: track-level metrics are averaged
within each dataset before group-level inference, and each dataset receives
equal weight regardless of its track count. Parameter distributions show one
point per dataset with a box over those points, so what is plotted is exactly
what is tested.

Two-group comparisons use a two-sided Mann-Whitney U test. Comparisons with
three or more groups use a Kruskal-Wallis test plus pairwise two-sided
Mann-Whitney U tests; with exactly three groups all three pairs are shown.
Pairwise p-values are Holm-adjusted within each metric. The statistics export
includes rank-biserial correlation or epsilon-squared effect sizes and reports
sample sizes as dataset counts.

Because the tests use dataset means, statistical power is set by the number of
datasets per group, not by the number of tracks. With three datasets per group
a two-sided Mann-Whitney U test cannot return a p-value below 0.1, so pairwise
comparisons will read `ns` however large the difference between groups looks.
Increase the number of independent datasets per group to detect pairwise
differences.

Directionality is the first-to-last displacement divided by total path length
and is bounded to `[0, 1]`. A fully stationary track has summary directionality
`0.0`; temporal samples before any movement have no defined directionality and
are excluded from temporal means. Final displacement angle plots report each
bin as a percentage of valid tracks in that group, with one shared radial scale
across groups.

MSD is averaged first across tracks within each dataset and then across
datasets. Curves show a two-sided 95% Student-t confidence interval and stop
when fewer than half of the group's datasets contribute to a lag. Curves are
not smoothed or forced to be monotonic.

The step turning-angle distribution histograms every step turning angle into
18 bins from 0 to 180 degrees per dataset, then shows the mean density across
datasets with a 95% confidence interval.

All trajectory panels share one spatial scale, computed across every selected
group, so representative tracks and centred trajectories can be compared
directly between panels and between groups. The scale is stated in each
figure title.

The cell-appearance figure counts only tracks with at least
`appearance_min_observations` observations (default 5), which keeps one- and
two-frame detections out of the counts. The "New tracks" panel starts after the
first imaged frame, because cells already present when imaging starts are not
arrivals.

Frames that are byte-identical copies of an earlier frame in the same folder
(for example `frame_001 (1).tif` next to `frame_001.tif`) are excluded from the
image sequence, and the dataset list shows how many were skipped. The files are
never deleted.

If startup is interrupted during the first installation, start the application
again. If it still fails, send the files in `workspace/logs/` to technical
support.
