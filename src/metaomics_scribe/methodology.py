"""Methodology template loader.

Each supported pipeline has a methodology YAML in ``methods/<pipeline_id>.yaml``
carrying per-stage methods-section prose lifted from the pipeline's
applications note. The drafter uses it to expand the "Bioinformatic Pipeline"
subsection of Methods with a bold-lead-in paragraph per completed stage.

The methodology is pipeline-specific and journal-agnostic — orthogonal to
``journals/<id>.yaml`` (journal style + IMRaD structure).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class MethodologyMeta(_Model):
    id: str
    source: str | None = None
    source_date: str | None = None


class PipelineOverview(_Model):
    overview: str


class StageMethod(_Model):
    """Per-stage methods prose."""

    title: str
    prose: str


class Methodology(_Model):
    methodology: MethodologyMeta
    pipeline: PipelineOverview
    stages: dict[str, StageMethod] = {}

    def for_stage(self, stage_id: str) -> StageMethod | None:
        return self.stages.get(stage_id)


def load_methodology(path: str | Path) -> Methodology:
    """Load and validate a methodology YAML."""
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Methodology.model_validate(data)
