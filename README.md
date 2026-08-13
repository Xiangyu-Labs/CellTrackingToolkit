# Cell Tracking Studio

Cell Tracking Studio is a local application for cell segmentation, trajectory tracking, and group comparison. Researchers do not need to configure Python, Conda, or Git manually. The first launch creates an isolated environment, installs dependencies, and downloads the segmentation model automatically.

## One-click startup

An internet connection is required for the first installation. The process usually takes several minutes and may download several GB of scientific computing dependencies such as Ultralytics and PyTorch, plus the approximately 137 MiB segmentation model. After installation, later launches reuse the local environment and download cache.

Every launch checks for a new application release. Git clones are updated only when the current branch is `main`, tracked files have no local changes, and the update can be applied as a fast-forward. Downloaded ZIP copies update from verified GitHub Release packages. If the update server is unavailable, the installed version starts normally.

### macOS

Double-click `start.command`.

If macOS blocks it the first time, right-click `start.command`, select **Open**, and confirm once more. The browser opens automatically after startup completes.

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

Place experimental images in `Datasets/`. Application updates never replace or delete `Datasets/`, `models/`, or `workspace/`.

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
