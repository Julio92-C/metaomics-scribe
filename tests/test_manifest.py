"""Manifest loader tests, exercising the contract end-to-end against the
checked-in example."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from metaomics_scribe.manifest import (
    Manifest,
    SUPPORTED_MAJOR,
    UnsupportedManifestVersion,
    load_manifest,
)

EXAMPLE = Path(__file__).parent.parent / "examples" / "manifest.example.json"


def test_loads_example_end_to_end():
    m = load_manifest(EXAMPLE)
    assert m.manifest_version == "1.0"
    assert m.study.id == "chicken_batch2"
    assert m.study.n_samples == 32
    assert m.config.stats.permanova_permutations == 9999
    assert "alpha_diversity" in m.stages
    assert m.stages["alpha_diversity"].status == "complete"


def test_table_schema_field_alias():
    """The JSON key `schema` is aliased to `table_schema` so it doesn't shadow
    BaseModel internals. Both names must work."""
    m = load_manifest(EXAMPLE)
    alpha_table = m.stages["alpha_diversity"].tables[0]
    assert alpha_table.table_schema is not None
    assert "shannon_diversity_index" in alpha_table.table_schema


def test_figure_optional_annotations():
    """Figures may carry kind-specific annotations (metric, pair, groups)."""
    m = load_manifest(EXAMPLE)
    shannon = next(
        f for f in m.stages["alpha_diversity"].figures if f.metric == "Shannon diversity index"
    )
    assert shannon.kind == "alpha_violin"
    assert shannon.groups is not None and "Dulse_W4" in shannon.groups

    volcano = m.stages["differential_abundance"].figures[0]
    assert volcano.kind == "volcano"
    assert volcano.pair == ["Control_W4", "Dulse_W4"]


def test_path_resolution():
    """Stage paths resolve as manifest_dir / outputs.project_root / stage.path."""
    m = load_manifest(EXAMPLE)
    fig = m.stages["alpha_diversity"].figures[0]
    resolved = m.resolve_path(fig.path)
    assert resolved.is_absolute()
    assert resolved.name == "shannon_diversity_index_violin.png"
    # outputs.project_root is "../..", so the resolved path lives outside
    # the examples/ directory.
    assert "examples" not in resolved.parts


def test_unsupported_major_rejected(tmp_path: Path):
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["manifest_version"] = f"{SUPPORTED_MAJOR + 1}.0"
    bad = tmp_path / "manifest.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(UnsupportedManifestVersion):
        load_manifest(bad)


def test_unparseable_version_rejected(tmp_path: Path):
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["manifest_version"] = "not-a-version"
    bad = tmp_path / "manifest.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(UnsupportedManifestVersion):
        load_manifest(bad)


def test_unknown_minor_field_is_tolerated(tmp_path: Path):
    """A new optional field in a future MINOR must not break older builds."""
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["study"]["future_optional_field"] = "ignored-by-this-build"
    data["manifest_version"] = f"{SUPPORTED_MAJOR}.99"
    ok = tmp_path / "manifest.json"
    ok.write_text(json.dumps(data), encoding="utf-8")
    m = load_manifest(ok)
    assert m.manifest_version == f"{SUPPORTED_MAJOR}.99"


def test_directory_path_accepted(tmp_path: Path):
    """`load_manifest` accepts either the file or its parent directory."""
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    (tmp_path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    m = load_manifest(tmp_path)
    assert m.study.id == "chicken_batch2"


def test_skipped_stage_loads(tmp_path: Path):
    """A stage with `status: skipped` and empty/absent artifact arrays loads cleanly."""
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["stages"]["virulome"] = {"status": "skipped"}
    out = tmp_path / "manifest.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    m = load_manifest(out)
    assert m.stages["virulome"].status == "skipped"
    assert m.stages["virulome"].tables == []
    assert m.stages["virulome"].figures == []


def test_in_memory_manifest_has_no_dir():
    """A Manifest built without load_manifest() cannot resolve paths."""
    m = load_manifest(EXAMPLE)
    fresh = Manifest.model_validate(m.model_dump(mode="json"))
    with pytest.raises(RuntimeError, match="not loaded from a file"):
        fresh.resolve_path("Datasets/x.csv")


def test_missing_required_field_raises(tmp_path: Path):
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    del data["study"]["primary_group_col"]
    bad = tmp_path / "manifest.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_manifest(bad)
