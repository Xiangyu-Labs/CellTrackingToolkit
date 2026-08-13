# Cell Tracking Studio

Cell Tracking Studio is a local application for cell segmentation, trajectory tracking, and group comparison. Researchers do not need to configure Python, Conda, or Git manually. The first launch creates an isolated environment, installs dependencies, and downloads the segmentation model automatically.

## One-click startup

An internet connection is required for the first installation. The process usually takes several minutes and may download several GB of scientific computing dependencies such as Ultralytics and PyTorch, plus the approximately 137 MiB segmentation model. After installation, later launches reuse the local environment and download cache.

Every launch checks for a new application release. Git clones are updated only when the current branch is `main`, tracked files have no local changes, and the update can be applied as a fast-forward. Downloaded ZIP copies update from verified GitHub Release packages. If the update server is unavailable, the installed version starts normally.

### macOS

Double-click `start.command`.

Because the downloaded ZIP is not signed and notarized with an Apple Developer
certificate, macOS may show **"start.command Not Opened"** the first time:

1. Click **Done**. Do not click **Move to Trash**.
2. In Finder, Control-click or right-click `start.command`.
3. Select **Open**, then confirm **Open** again.

If the **Open** option is unavailable, open **System Settings → Privacy &
Security**, scroll down, and select **Open Anyway** for `start.command`.

As a fallback, open Terminal and run the following command, replacing the path
with the location of the extracted application folder. You can type the command
and then drag the folder from Finder into the Terminal window to insert its
path:

```sh
xattr -dr com.apple.quarantine "/path/to/cell-tracking-studio"
```

Then double-click `start.command` again. This approval is normally required
only once. The browser opens automatically after startup completes.

### Windows

Double-click `start.bat`. Python, Conda, and Git do not need to be installed in advance.

### Linux

Double-click `start-linux.desktop`. If the desktop environment asks for permission, select **Allow Launching**. You can also run this command from the project directory:

```sh
./start.sh
```

## Experimental resources

The first launch downloads the laboratory segmentation model to:

```text
models/segmentation/yolo11x-seg.pt
```

The launcher verifies the model's exact file size and SHA-256 checksum. It does not substitute a general-purpose YOLO model, because doing so could produce incorrect scientific results.

Place experimental images in the included `Datasets/` folder. Each folder
directly containing images is treated as one dataset. For example:

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

In this example, `Sample_01`, `Sample_02`, and `Sample_03` are datasets;
`Control` and `Treatment` are optional grouping folders. Images can also be
placed directly inside a single folder under `Datasets/`.

Application updates never replace or delete `Datasets/`, `models/`, or
`workspace/`. Experimental images inside `Datasets/` are ignored by Git and
are not included in application releases.

To use a different laboratory model, set `CELLTRACK_WEIGHTS_PATH` to its absolute path. A custom model is checked for existence but is never downloaded or overwritten by the updater.

To skip the application update check for one launch, set:

```sh
CELLTRACK_SKIP_UPDATE=1
```

## Startup failures

The launcher automatically detects and repairs a damaged, incomplete, or transferred `.venv`. If installation is interrupted, double-click the launcher again.

If startup still fails, send these logs to technical support:

```text
workspace/logs/bootstrap.log
workspace/logs/launcher.log
workspace/logs/server.log
```

You do not need to delete experimental data, models, or analysis results manually.
