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

## Analysis methods

Every figure type is exported by default: cell appearance, temporal trends
(long tracks and all tracks), trajectory classification, turning-angle
variability distribution, MSD (long tracks, all tracks, and the standalone MSD
summary), representative trajectories, group trajectory analysis, and a 3 x 3
grid of parameter distributions. Figure filenames are numbered in a fixed
canonical order, so a given figure keeps its `figure_NN_` prefix no matter
which other figures are selected. New analyses also include paper-ready
composite PNGs whenever their component figures are selected; existing results
in `workspace/analysis/` are left unchanged.

The temporal grid and the parameter-distribution grid are paired: panel *i* on
the left of `paper_02` shows the per-frame trend for the same quantity that
panel *i* on the right summarises per track. Both grids are 3 x 3.

Parameter distributions are drawn as SuperPlots. The violin shows the
distribution over individual tracks, and the large outlined points are the mean
of each dataset. Statistics are computed on those dataset means, so each
dataset counts as one independent experimental replicate regardless of how many
tracks it contributed. Axis labels report both counts as
`n=<tracks> (<datasets>)`.

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

MSD is pooled across every track at each lag and shown as the mean with its
standard error; the standalone MSD summary additionally reports a two-sided 95%
Student-t confidence interval and stops when fewer than 20% of the group's
tracks contribute to a lag. Curves are not smoothed or forced to be monotonic.

All trajectory panels share one spatial scale, computed across every selected
group, so representative tracks and centred trajectories can be compared
directly between panels and between groups. The scale is stated in each
figure title.

The cell-appearance figure counts only tracks with at least
`appearance_min_observations` observations (default 5), which keeps one- and
two-frame detections from appearing as a spike of arrivals on the first frame.

Frames that are byte-identical copies of an earlier frame in the same folder
(for example `frame_001 (1).tif` next to `frame_001.tif`) are excluded from the
image sequence, and the dataset list shows how many were skipped. The files are
never deleted.

If startup is interrupted during the first installation, start the application
again. If it still fails, send the files in `workspace/logs/` to technical
support.
