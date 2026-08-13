# Add experimental images here

Drag each image sequence into its own folder inside `Datasets/`. A folder that
directly contains supported image files is detected as one dataset.

Supported image formats:

- `.bmp`
- `.jpeg`
- `.jpg`
- `.png`
- `.tif`
- `.tiff`

Example:

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

Here, the three `Sample_*` folders are datasets. `Control` and `Treatment` are
optional grouping folders.

Application updates do not replace or delete anything in `Datasets/`.
