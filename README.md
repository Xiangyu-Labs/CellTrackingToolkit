# Cell Tracking Studio

Cell Tracking Studio is a local application for cell segmentation, trajectory tracking, and group comparison. Researchers do not need to configure Python or Conda manually; the first launch creates an isolated environment and installs the dependencies automatically.

## One-click startup

An internet connection is required for the first installation. The process usually takes several minutes and may download several GB of scientific computing dependencies such as Ultralytics and PyTorch. After installation, later launches reuse the local environment and download cache.

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

## Required experimental resources

The project requires the segmentation model supplied by the laboratory:

```text
models/segmentation/yolo11x-seg.pt
```

The launcher does not substitute a general-purpose YOLO model for the experimental model, because doing so could produce incorrect scientific results. Place experimental images in `Datasets/`.

## Startup failures

The launcher automatically detects and repairs a damaged, incomplete, or transferred `.venv`. If installation is interrupted, double-click the launcher again.

If startup still fails, send these logs to technical support:

```text
workspace/logs/bootstrap.log
workspace/logs/launcher.log
workspace/logs/server.log
```

You do not need to delete experimental data, models, or analysis results manually.
