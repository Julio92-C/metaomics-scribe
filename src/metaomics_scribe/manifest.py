"""Manifest loader, validator, and path resolver.

The single contract between an upstream metagenomics pipeline and the agent.
Reads `manifest.json`, validates it against the schema in
`docs/MANIFEST_SCHEMA.md`, and resolves stage-relative paths through
`outputs.project_root`.

Path resolution rule (matches the schema doc): an artifact's absolute path is
`manifest_dir / outputs.project_root / stage_entry.path`. This keeps stage
paths short and human-readable while remaining portable when the project
directory is copied between machines.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

SUPPORTED_MAJOR = 1
"""The manifest MAJOR version this build understands. See schema §Versioning."""

StageStatus = Literal["complete", "skipped", "failed"]


class UnsupportedManifestVersion(Exception):
    """Raised when `manifest_version`'s MAJOR is not `SUPPORTED_MAJOR`."""


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class Study(_Model):
    id: str
    name: str
    description: str | None = None
    primary_group_col: str
    random_effect: str | None
    fixed_effects: list[str]
    group_levels: list[str]
    n_samples: int
    n_controls: int
    control_ids: list[str] | None = None


class Filters(_Model):
    min_count_per_sample: int
    min_count_for_species: int
    min_prevalence_aldex: int


class Stats(_Model):
    permanova_permutations: int
    aldex_mc_samples: int
    alpha: float


class Config(_Model):
    filters: Filters
    stats: Stats


class Outputs(_Model):
    project_root: str
    datasets_dir: str
    figures_dir: str
    log_file: str | None = None


class Table(_Model):
    path: str
    kind: str
    format: str
    table_schema: dict[str, str] | None = Field(default=None, alias="schema")
    row_count: int | None = None
    description: str | None = None


class Figure(_Model):
    path: str
    kind: str
    caption_seed: str | None = None
    width_px: int | None = None
    height_px: int | None = None
    dpi: int | None = None
    format: str | None = None
    groups: list[str] | None = None
    metric: str | None = None
    pair: list[str] | None = None
    domain: str | None = None
    group: str | None = None
    # Pre-stitched manuscript composites (kind="panel_composite") emitted by
    # the `panels` stage carry these two fields to identify which manuscript
    # slot they fill and which section (main / supplementary) they belong to.
    slot: str | None = None
    section: str | None = None


class StatsText(_Model):
    path: str
    kind: str
    primary_var: str | None = None
    description: str | None = None


class Stage(_Model):
    status: StageStatus
    duration_s: float | None = None
    tables: list[Table] = []
    figures: list[Figure] = []
    stats_text: list[StatsText] = []


class Pipeline(_Model):
    name: str
    repo: str
    version: str
    run_started_at: datetime | None = None
    run_finished_at: datetime | None = None


PANELS_STAGE = "panels"
PANEL_COMPOSITE_KIND = "panel_composite"
SUPPLEMENTARY_TABLES_KIND = "supplementary_tables"


class Manifest(_Model):
    manifest_version: str
    study: Study
    config: Config
    outputs: Outputs
    stages: dict[str, Stage]
    pipeline: Pipeline

    _manifest_dir: Path | None = PrivateAttr(default=None)

    def find_panel(self, slot_id: str) -> Figure | None:
        """Return the panel composite Figure for ``slot_id``, or ``None``.

        Iterates the ``panels`` stage's figures (``kind == "panel_composite"``)
        and matches on the ``slot`` field. Returns ``None`` when the slot id
        isn't emitted or the `panels` stage is absent — callers decide whether
        that's a hard error.
        """
        stage = self.stages.get(PANELS_STAGE)
        if stage is None:
            return None
        for fig in stage.figures:
            if fig.kind == PANEL_COMPOSITE_KIND and fig.slot == slot_id:
                return fig
        return None

    def panel_slot_ids(self) -> list[str]:
        """Return all panel slot ids emitted by the `panels` stage, in order."""
        stage = self.stages.get(PANELS_STAGE)
        if stage is None:
            return []
        return [
            fig.slot
            for fig in stage.figures
            if fig.kind == PANEL_COMPOSITE_KIND and fig.slot is not None
        ]

    def find_panel_table(self, table_kind: str) -> Table | None:
        """Return the first table in the `panels` stage with the given kind.

        Used to surface manuscript-level tables the pipeline composes itself
        (currently the multi-sheet ``supplementary_tables.xlsx``). Returns
        ``None`` when the kind isn't emitted or the `panels` stage is absent.
        """
        stage = self.stages.get(PANELS_STAGE)
        if stage is None:
            return None
        for table in stage.tables:
            if table.kind == table_kind:
                return table
        return None

    @property
    def manifest_dir(self) -> Path:
        """Directory containing the loaded `manifest.json`. None until loaded."""
        if self._manifest_dir is None:
            raise RuntimeError(
                "Manifest was constructed in-memory, not loaded from a file; "
                "set _manifest_dir or use load_manifest() to enable path resolution."
            )
        return self._manifest_dir

    def resolve_path(self, stage_relative_path: str) -> Path:
        """Resolve a stage entry's `path` to an absolute filesystem location.

        Applies the rule from the schema doc: `manifest_dir / outputs.project_root
        / stage_relative_path`. Does not check that the resolved path exists —
        callers handle missing files (a stage may have been deleted post-run).
        """
        return (self.manifest_dir / self.outputs.project_root / stage_relative_path).resolve()


def _check_version(version: str) -> None:
    """Reject a manifest whose MAJOR version this build does not understand."""
    major_part = version.split(".", 1)[0]
    try:
        major = int(major_part)
    except ValueError as exc:
        raise UnsupportedManifestVersion(
            f"manifest_version {version!r} is not parseable as MAJOR.MINOR"
        ) from exc
    if major != SUPPORTED_MAJOR:
        raise UnsupportedManifestVersion(
            f"manifest_version {version!r} (MAJOR={major}) is not supported; "
            f"this build understands MAJOR={SUPPORTED_MAJOR} only"
        )


def load_manifest(path: str | Path) -> Manifest:
    """Load and validate a `manifest.json` from a file or its parent directory.

    `path` may be the manifest file itself or a directory containing one (the
    schema doc says the agent accepts either). MAJOR mismatches raise
    `UnsupportedManifestVersion`; everything else surfaces as Pydantic
    `ValidationError`.
    """
    p = Path(path)
    if p.is_dir():
        p = p / "manifest.json"
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    _check_version(data.get("manifest_version", ""))
    manifest = Manifest.model_validate(data)
    manifest._manifest_dir = p.parent.resolve()
    return manifest
