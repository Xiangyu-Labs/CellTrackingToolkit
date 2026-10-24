from __future__ import annotations

import threading

from celltrack.analysis.compute import analysis_to_csv, prepare_analysis, statistics_to_csv
from celltrack.analysis.figures import render_all_figures
from celltrack.analysis.models import AnalysisParameters
from celltrack.analysis.storage import AnalysisStore, analysis_store
from celltrack.analysis.style import FigureStyle
from celltrack.datasets import require_datasets, tracking_complete
from celltrack.datasets.repository import dataset_map


generation_lock = threading.Lock()


def create_analysis(
    groups: list[tuple[str, list[str]]],
    parameters: AnalysisParameters,
    store: AnalysisStore = analysis_store,
    style: FigureStyle | None = None,
    derived_from: str | None = None,
) -> dict[str, object]:
    style = style or FigureStyle()
    names = [name.strip() for name, _ids in groups]
    if len(set(name.casefold() for name in names)) != len(names):
        raise ValueError("Group names must be unique")
    selected_ids = [dataset_id for _name, dataset_ids in groups for dataset_id in dataset_ids]
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("Each dataset can belong to only one comparison group")
    prepared = []
    group_metadata: list[dict[str, object]] = []
    for name, (_raw_name, dataset_ids) in zip(names, groups):
        datasets = require_datasets(dataset_ids)
        missing = [dataset.relative_path for dataset in datasets if not tracking_complete(dataset)]
        if missing:
            raise ValueError(f"Run tracking first: {', '.join(missing)}")
        prepared.append((name, [(dataset.relative_path, dataset.tracking_csv) for dataset in datasets]))
        group_metadata.append({"name": name, "datasets": [dataset.relative_path for dataset in datasets]})

    bundle = prepare_analysis(prepared, parameters)
    if not bundle.tracks:
        raise ValueError("The selected datasets contain no valid tracks")
    with generation_lock:
        images = render_all_figures(bundle, style)
    for group in group_metadata:
        group["tracks"] = len(bundle.summaries_for(str(group["name"]), prefer_long=False))
    return store.create(
        images,
        analysis_to_csv(bundle),
        group_metadata,
        parameters.model_dump(),
        statistics_content=statistics_to_csv(bundle),
        style=style.model_dump(),
        derived_from=derived_from,
    )


def rerender_groups(
    manifest: dict[str, object],
    order: list[tuple[int, str]],
) -> list[tuple[str, list[str]]]:
    """Map a stored analysis back to dataset ids, renamed and reordered.

    ``order`` lists (index of the original group, new name). It must use every
    original group exactly once: dropping or merging groups would change the
    statistics, which a re-render must never do.
    """
    groups = list(manifest["groups"])
    indexes = [index for index, _name in order]
    if sorted(indexes) != list(range(len(groups))):
        raise ValueError("Re-rendering keeps every group exactly once; only names and order can change")
    by_path = {dataset.relative_path: dataset.id for dataset in dataset_map().values()}
    result = []
    for index, name in order:
        paths = [str(path) for path in groups[index]["datasets"]]
        missing = [path for path in paths if path not in by_path]
        if missing:
            raise ValueError(f"These datasets are no longer available: {', '.join(missing)}")
        result.append((name, [by_path[path] for path in paths]))
    return result
