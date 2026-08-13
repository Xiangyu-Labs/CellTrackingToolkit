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

If startup is interrupted during the first installation, start the application
again. If it still fails, send the files in `workspace/logs/` to technical
support.
