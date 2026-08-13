from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


TEMPORAL_METRICS = {
    "instantaneous_speed": "Instantaneous speed (px/frame)",
    "step_distance": "Step distance (px)",
    "net_displacement": "Net displacement (px)",
    "directionality": "Directionality",
    "turning_angle": "Turning angle (deg)",
    "area": "Cell area (px2)",
    "perimeter": "Perimeter (px)",
    "aspect_ratio": "Aspect ratio",
    "roundness": "Roundness",
    "shape_change_rate": "Shape change rate",
}

SUMMARY_METRICS = {
    "total_path_length": "Total path length (px)",
    "net_displacement": "Net displacement (px)",
    "mean_speed": "Mean speed (px/frame)",
    "directionality": "Directionality",
    "mean_turning_angle": "Mean turning angle (deg)",
    "mean_area": "Mean area (px2)",
    "mean_perimeter": "Mean perimeter (px)",
    "mean_roundness": "Mean roundness",
    "mean_shape_change_rate": "Shape change rate",
}

FIGURE_TYPES = {
    "cell_appearance": "Cell appearance",
    "temporal_long": "Temporal trends (long tracks)",
    "classification": "Trajectory classification",
    "turning_angle_distribution": "Turning-angle variability distribution",
    "msd_long": "Mean square displacement (long tracks)",
    "msd_summary": "MSD comparison (mean ± 95% CI)",
    "representatives": "Representative trajectories for each group",
    "temporal_all": "Temporal trends (all tracks)",
    "msd_all": "Mean square displacement (all tracks)",
    "group_trajectories": "Trajectory analysis for each group",
    "parameter_distributions": "Parameter distributions",
}


class AnalysisParameters(BaseModel):
    long_track_min_observations: int = Field(default=50, ge=1, le=10000)
    fallback_to_all_tracks: bool = True
    random_directionality_max: float = Field(default=0.2, ge=0, le=1)
    directed_directionality_min: float = Field(default=0.5, ge=0, le=1)
    msd_max_lag: int = Field(default=50, ge=1, le=10000)
    msd_fit_points: int = Field(default=20, ge=2, le=10000)
    msd_min_fit_points: int = Field(default=4, ge=2, le=10000)
    angle_bins: int = Field(default=18, ge=4, le=180)
    representatives_per_type: int = Field(default=4, ge=1, le=12)
    frame_interval_minutes: float | None = Field(default=None, gt=0)
    microns_per_pixel: float | None = Field(default=None, gt=0)
    temporal_metrics: list[str] = Field(default_factory=lambda: list(TEMPORAL_METRICS))
    summary_metrics: list[str] = Field(default_factory=lambda: list(SUMMARY_METRICS))
    figure_types: list[str] = Field(default_factory=lambda: list(FIGURE_TYPES))

    @model_validator(mode="after")
    def validate_combinations(self) -> "AnalysisParameters":
        if self.random_directionality_max >= self.directed_directionality_min:
            raise ValueError("random directionality threshold must be lower than directed threshold")
        if self.msd_min_fit_points > self.msd_fit_points:
            raise ValueError("MSD minimum fit points cannot exceed fit points")
        self._validate_selection("temporal_metrics", self.temporal_metrics, TEMPORAL_METRICS)
        self._validate_selection("summary_metrics", self.summary_metrics, SUMMARY_METRICS)
        self._validate_selection("figure_types", self.figure_types, FIGURE_TYPES, allow_empty=False)
        if ({"temporal_long", "temporal_all"} & set(self.figure_types)) and not self.temporal_metrics:
            raise ValueError("temporal metrics are required for temporal figures")
        if "parameter_distributions" in self.figure_types and not self.summary_metrics:
            raise ValueError("summary metrics are required for parameter distributions")
        return self

    @staticmethod
    def _validate_selection(name: str, values: list[str], catalog: dict[str, str], allow_empty: bool = True) -> None:
        if not allow_empty and not values:
            raise ValueError(f"{name} must contain at least one item")
        if len(values) != len(set(values)):
            raise ValueError(f"{name} contains duplicate items")
        unknown = set(values) - set(catalog)
        if unknown:
            raise ValueError(f"Unknown {name}: {', '.join(sorted(unknown))}")


DEFAULT_ANALYSIS_PARAMETERS = AnalysisParameters()


def analysis_options() -> dict[str, object]:
    return {
        "defaults": DEFAULT_ANALYSIS_PARAMETERS.model_dump(),
        "ranges": {
            "long_track_min_observations": [1, 10000],
            "random_directionality_max": [0, 1],
            "directed_directionality_min": [0, 1],
            "msd_max_lag": [1, 10000],
            "msd_fit_points": [2, 10000],
            "msd_min_fit_points": [2, 10000],
            "angle_bins": [4, 180],
            "representatives_per_type": [1, 12],
            "frame_interval_minutes": [0, None],
            "microns_per_pixel": [0, None],
        },
        "temporal_metrics": TEMPORAL_METRICS,
        "summary_metrics": SUMMARY_METRICS,
        "figure_types": FIGURE_TYPES,
    }
