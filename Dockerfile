# Server image for running Cell Tracking Studio behind a reverse proxy.
# Datasets, workspace and model weights are mounted at run time.
FROM ghcr.io/astral-sh/uv:0.11.16 AS uv

FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    YOLO_CONFIG_DIR=/tmp/ultralytics \
    CELLTRACK_SKIP_UPDATE=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev --python /usr/local/bin/python3.11

COPY VERSION run.py ./
COPY src ./src
ENV PYTHONPATH=/app/src

EXPOSE 8000
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8000", "--no-browser"]
