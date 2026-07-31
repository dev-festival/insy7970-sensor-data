from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
import hashlib
import json


@dataclass(frozen=True)
class FeatureSpaceSpec:
    name: str
    label: str
    dimension: str
    prefix: str
    axis: str | None = None


@dataclass(frozen=True)
class ActiveModelPolicy:
    name: str
    feature_policy_version: str
    scaler_policy: str
    algorithm: str
    alignment_policy: str
    k: int
    random_seed: int
    max_iterations: int
    tolerance: float
    pca_iterations: int
    minimum_feature_coverage: float
    feature_spaces: tuple[FeatureSpaceSpec, ...]

    @property
    def version(self) -> str:
        payload = json.dumps(self.version_payload(), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        return f"{self.name}:{digest}"

    def version_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "feature_policy_version": self.feature_policy_version,
            "scaler_policy": self.scaler_policy,
            "algorithm": self.algorithm,
            "alignment_policy": self.alignment_policy,
            "k": self.k,
            "random_seed": self.random_seed,
            "max_iterations": self.max_iterations,
            "tolerance": self.tolerance,
            "pca_iterations": self.pca_iterations,
            "minimum_feature_coverage": self.minimum_feature_coverage,
            "feature_spaces": [asdict(spec) for spec in self.feature_spaces],
            "dimension_mapping": self.dimension_mapping,
            "temperature_metrics": ["temp_ambient", "temp_sensor"],
        }

    def public_payload(self) -> dict[str, Any]:
        return {
            **self.version_payload(),
            "version": self.version,
            "feature_spaces": [
                {
                    "name": spec.name,
                    "label": spec.label,
                    "dimension": spec.dimension,
                }
                for spec in self.feature_spaces
            ],
        }

    @property
    def feature_specs(self) -> dict[str, FeatureSpaceSpec]:
        return {spec.name: spec for spec in self.feature_spaces}

    @property
    def dimension_mapping(self) -> dict[str, str]:
        return {
            "x": "x_accel",
            "y": "y_vel",
            "z": "z_vel",
            "temperature": "temperature",
        }

    def feature_space_for(
        self,
        *,
        metric: str | None = None,
        dimension: str | None = None,
        requested: str | None = None,
    ) -> FeatureSpaceSpec:
        if requested not in (None, ""):
            selected = str(requested).strip().lower()
            spec = self.feature_specs.get(selected)
            if spec is None:
                allowed = ", ".join(sorted(self.feature_specs))
                raise ValueError(
                    f"feature_space must be active under policy {self.version}: {allowed}"
                )
            return spec
        selected_metric = str(metric or "").strip().lower()
        selected_dimension = str(dimension or "x").strip().lower()
        mapping_key = (
            "temperature"
            if selected_metric in {"temp_ambient", "temp_sensor"}
            else selected_dimension
        )
        feature_space = self.dimension_mapping.get(mapping_key)
        if feature_space is None:
            allowed = ", ".join(sorted(self.dimension_mapping))
            raise ValueError(f"dimension must be one of: {allowed}")
        return self.feature_specs[feature_space]

    def validate_k(self, requested: int | None) -> int:
        if requested is not None and int(requested) != self.k:
            raise ValueError(
                f"k is service-owned by active policy {self.version}; expected {self.k}, "
                f"received {requested}."
            )
        return self.k


ACTIVE_MODEL_POLICY = ActiveModelPolicy(
    name="registered_model_v2",
    feature_policy_version="active_store_features_v2",
    scaler_policy="standard_zscore_v1",
    algorithm="deterministic_kmeans",
    alignment_policy="nearest_scaled_centroid_v1",
    k=5,
    random_seed=42,
    max_iterations=100,
    tolerance=1e-6,
    pca_iterations=50,
    minimum_feature_coverage=0.2,
    feature_spaces=(
        FeatureSpaceSpec(
            name="x_accel",
            label="X Acceleration",
            dimension="x",
            prefix="rms_accel_",
            axis="x",
        ),
        FeatureSpaceSpec(
            name="y_vel",
            label="Y Velocity",
            dimension="y",
            prefix="rms_vel_",
            axis="y",
        ),
        FeatureSpaceSpec(
            name="z_vel",
            label="Z Velocity",
            dimension="z",
            prefix="rms_vel_",
            axis="z",
        ),
        FeatureSpaceSpec(
            name="temperature",
            label="Temperature",
            dimension="temperature",
            prefix="temp_sensor_",
        ),
    ),
)

