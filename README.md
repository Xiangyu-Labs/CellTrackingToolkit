# Cell Tracking Studio

Cell Tracking Studio 是用于细胞分割、轨迹追踪和组间比较的本地软件。无需科研人员手动配置 Python 或 Conda；首次启动会自动建立隔离环境并安装依赖。

## 一键启动

首次安装需要联网，通常需要数分钟。Ultralytics、PyTorch 等科学计算依赖体积较大，可能下载数 GB。安装完成后，后续启动会直接复用本地环境和下载缓存。

### macOS

双击 `start.command`。

如果 macOS 第一次阻止运行，请右键点击 `start.command`，选择“打开”，再确认一次。启动完成后浏览器会自动打开。

### Windows

双击 `start.bat`。不需要预先安装 Python、Conda 或 Git。

### Linux

双击 `start-linux.desktop`。如果桌面环境询问权限，请选择“允许启动”。也可以在项目目录运行：

```sh
./start.sh
```

## 必需的实验资源

项目需要实验室提供的分割模型：

```text
models/segmentation/yolo11x-seg.pt
```

启动器不会用通用 YOLO 模型替换实验模型，以免产生错误的科研结果。实验图像放在 `Datasets/` 中。

## 启动失败

启动器会自动识别并修复损坏、未完成或从其他电脑复制来的 `.venv`。安装中断时，重新双击启动即可。

如果仍然失败，请把以下日志发送给技术人员：

```text
workspace/logs/bootstrap.log
workspace/logs/launcher.log
workspace/logs/server.log
```

不需要手动删除实验数据、模型或分析结果。
