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

The default export is the paper-focused set: cell appearance, long-track
temporal trends, trajectory classification, step turning-angle distribution,
long-track MSD, representative trajectories, group trajectory analysis, and a
3 x 3 grid of dataset-level parameter distributions. All-track temporal and
MSD figures and the standalone MSD summary remain available as optional
figures. New analyses also include paper-ready composite PNGs whenever their
component figures are selected; existing results in `workspace/analysis/` are
left unchanged.

Each dataset is treated as one independent experimental replicate. Tracks
within a dataset are technical observations: track-level metrics are averaged
within each dataset before group-level inference, and each dataset receives
equal weight regardless of its track count.

Two-group comparisons use a two-sided Mann-Whitney U test. Comparisons with
three or more groups use a Kruskal-Wallis test plus pairwise two-sided
Mann-Whitney U tests. Pairwise p-values are Holm-adjusted within each metric.
The statistics export includes rank-biserial correlation or epsilon-squared
effect sizes and reports sample sizes as dataset counts.

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

If startup is interrupted during the first installation, start the application
again. If it still fails, send the files in `workspace/logs/` to technical
support.
