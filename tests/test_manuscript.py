"""ManuscriptDrafter tests — methods section is deterministic, so we can
assert exact field-to-prose traceability."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from metaomics_scribe.journal import load_journal
from metaomics_scribe.manifest import load_manifest
from metaomics_scribe.manuscript import ManuscriptDrafter
from metaomics_scribe.methodology import load_methodology

REPO = Path(__file__).parent.parent
EXAMPLE_MANIFEST = REPO / "examples" / "manifest.example.json"
FRONTIERS_JOURNAL = REPO / "journals" / "frontiers_microbiome.yaml"
PIPELINE_METHODOLOGY = REPO / "methods" / "metagenomics_pipeline_automation.yaml"


@pytest.fixture
def example_manifest(tmp_path: Path) -> Path:
    """Materialise the example manifest in tmp_path so resolve_path() works."""
    data = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    manifest_dir = tmp_path / "project" / "run"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    return manifest_dir / "manifest.json"


def _draft(example_manifest: Path, tmp_path: Path) -> tuple[ManuscriptDrafter, Path]:
    m = load_manifest(example_manifest)
    j = load_journal(FRONTIERS_JOURNAL)
    drafter = ManuscriptDrafter(m, j, output_root=tmp_path / "runs")
    drafted = drafter.draft()
    return drafter, drafted.markdown_path


# ---------------------------------------------------------------------------
# methods section content (deterministic — no LLM)
# ---------------------------------------------------------------------------


def test_methods_section_numbers_come_from_manifest(example_manifest: Path, tmp_path: Path):
    """Every quantitative claim in methods must trace back to a manifest field
    (the `CLAUDE.md` no-invented-numbers invariant)."""
    m = load_manifest(example_manifest)
    _, md_path = _draft(example_manifest, tmp_path)
    text = md_path.read_text(encoding="utf-8")

    # Extract the methods block.
    start = text.index("## Materials and Methods")
    methods = text[start:]

    # Sample / control counts — appear verbatim.
    assert f"{m.study.n_samples} biological sample" in methods
    assert str(m.study.n_controls) in methods

    # Group levels — every one is mentioned.
    for level in m.study.group_levels:
        assert level in methods, f"group level {level!r} missing from methods"

    # Filter and stats parameters — all six appear verbatim.
    assert str(m.config.filters.min_count_per_sample) in methods
    assert str(m.config.filters.min_count_for_species) in methods
    assert str(m.config.filters.min_prevalence_aldex) in methods
    assert str(m.config.stats.aldex_mc_samples) in methods
    assert f"{m.config.stats.permanova_permutations:,}" in methods
    assert str(m.config.stats.alpha) in methods

    # Pipeline provenance — name, version, repo.
    assert m.pipeline.name in methods
    assert m.pipeline.version in methods
    assert m.pipeline.repo in methods


def test_methods_section_has_required_subsections(example_manifest: Path, tmp_path: Path):
    """Frontiers methods carries four canonical subsections."""
    _, md_path = _draft(example_manifest, tmp_path)
    text = md_path.read_text(encoding="utf-8")

    for required_heading in (
        "### Study Design",
        "### Sample Collection and Sequencing",
        "### Bioinformatic Pipeline",
        "### Statistical Analysis",
    ):
        assert required_heading in text, f"missing methods subsection: {required_heading}"


def test_no_invented_numbers_in_methods(example_manifest: Path, tmp_path: Path):
    """Every numeric token (integer, comma-grouped, or float) in the methods
    prose must appear verbatim in the manifest JSON. This is the structural
    guardrail for the CLAUDE.md no-fabrication invariant.

    Tokens with absolute value < 2 are skipped because integers 0/1 appear in
    grammar (one sample, etc.) and don't carry quantitative claims."""
    _, md_path = _draft(example_manifest, tmp_path)
    text = md_path.read_text(encoding="utf-8")
    methods = text[text.index("## Materials and Methods"):]
    next_h2 = methods.find("\n## ", 1)
    if next_h2 != -1:
        methods = methods[:next_h2]

    manifest_text = example_manifest.read_text(encoding="utf-8")
    # Match comma-grouped, floats, or integers — in that order so floats win
    # over their integer fragments (e.g. "0.05" not "0" + "05").
    found = re.findall(r"\d{1,3}(?:,\d{3})+|\d+\.\d+|\d+", methods)
    illegal = []
    for tok in found:
        # Skip tokens whose integer part is < 2 — those are grammar, not claims.
        try:
            magnitude = float(tok.replace(",", ""))
        except ValueError:
            continue
        if magnitude < 2:
            continue
        # Manifest stores integers raw (9999); methods prose formats with
        # thousands separators (9,999). Normalise the token's commas away
        # before checking presence in the manifest source.
        if tok not in manifest_text and tok.replace(",", "") not in manifest_text:
            illegal.append(tok)
    assert not illegal, (
        f"methods contains numeric tokens not present in the manifest: {illegal}"
    )


# ---------------------------------------------------------------------------
# results stub references routed figures by subsection
# ---------------------------------------------------------------------------


def test_results_stub_groups_by_subsection(example_manifest: Path, tmp_path: Path):
    """Results subsections in the stub list the slot ids whose `subsection`
    field points at them, drawn from the manifest panel composites."""
    _, md_path = _draft(example_manifest, tmp_path)
    text = md_path.read_text(encoding="utf-8")

    # community_overview pulls two main composites in the example manifest.
    assert "`fig01_taxa_overview`" in text
    assert "`fig05_relative_abundance_species`" in text

    # The supplementary composite has no `subsection` in the example manifest,
    # so it should appear under the "awaiting assignment" block.
    assert "Figures awaiting subsection assignment" in text
    assert "`figS00_heatmap_species`" in text


# ---------------------------------------------------------------------------
# output layout
# ---------------------------------------------------------------------------


def test_draft_writes_to_study_subdir(example_manifest: Path, tmp_path: Path):
    """manuscript.md lands at runs/<study_id>/manuscript.md."""
    _, md_path = _draft(example_manifest, tmp_path)
    assert md_path.name == "manuscript.md"
    assert md_path.parent.name == "chicken_batch2"
    assert md_path.parent.parent.name == "runs"


def test_unsupported_section_renders_stub(example_manifest: Path, tmp_path: Path):
    """Sections without a dedicated renderer fall through to a TODO stub."""
    _, md_path = _draft(example_manifest, tmp_path)
    text = md_path.read_text(encoding="utf-8")
    # Introduction has no renderer in v0.4.0; check it ships as a stub.
    assert "## Introduction" in text
    assert "TODO: introduction" in text


# ---------------------------------------------------------------------------
# DOCX export
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# methodology integration
# ---------------------------------------------------------------------------


def test_methodology_yaml_loads_and_covers_canonical_stages():
    """Shipped methodology covers every stage the example manifest declares."""
    m = load_manifest(EXAMPLE_MANIFEST)
    meth = load_methodology(PIPELINE_METHODOLOGY)
    complete = [name for name, s in m.stages.items() if s.status == "complete"]
    # 'panels' is in the manifest but not a methods-prose stage we care about
    # here; everything else with status=complete should have prose.
    missing = [name for name in complete if meth.for_stage(name) is None]
    assert not missing, f"methodology missing prose for completed stages: {missing}"


def test_methodology_renders_per_stage_paragraphs(example_manifest: Path, tmp_path: Path):
    """When a methodology is provided, the Bioinformatic Pipeline subsection
    gains one bold-lead-in paragraph per completed stage, in manifest order."""
    m = load_manifest(example_manifest)
    j = load_journal(FRONTIERS_JOURNAL)
    meth = load_methodology(PIPELINE_METHODOLOGY)
    drafter = ManuscriptDrafter(m, j, output_root=tmp_path / "runs", methodology=meth)
    text = drafter.draft().markdown_path.read_text(encoding="utf-8")

    # Each completed stage's title appears as a bold lead-in.
    for stage_name, stage in m.stages.items():
        if stage.status != "complete":
            continue
        entry = meth.for_stage(stage_name)
        if entry is None:
            continue
        assert f"**{entry.title}.**" in text, (
            f"missing methodology paragraph for stage {stage_name!r}"
        )


def test_methodology_optional_falls_back_cleanly(example_manifest: Path, tmp_path: Path):
    """Without a methodology, the generic Bioinformatic Pipeline paragraph
    still ships — nothing crashes."""
    m = load_manifest(example_manifest)
    j = load_journal(FRONTIERS_JOURNAL)
    drafter = ManuscriptDrafter(m, j, output_root=tmp_path / "runs", methodology=None)
    text = drafter.draft().markdown_path.read_text(encoding="utf-8")
    assert "### Bioinformatic Pipeline" in text
    # No methodology means no bold-lead-in paragraphs.
    assert "**Taxonomic profiling.**" not in text


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not on PATH")
def test_draft_docx_produces_docx(example_manifest: Path, tmp_path: Path):
    m = load_manifest(example_manifest)
    j = load_journal(FRONTIERS_JOURNAL)
    drafter = ManuscriptDrafter(m, j, output_root=tmp_path / "runs")
    drafted = drafter.draft_docx()
    assert drafted.docx_path is not None
    assert drafted.docx_path.exists()
    assert drafted.docx_path.suffix == ".docx"
    # DOCX is a zip — check the magic bytes.
    assert drafted.docx_path.read_bytes()[:2] == b"PK"


def test_draft_docx_raises_when_pandoc_missing(example_manifest: Path, tmp_path: Path, monkeypatch):
    """When pandoc isn't on PATH, draft_docx fails loudly rather than silently
    skipping the DOCX step."""
    monkeypatch.setattr("metaomics_scribe.manuscript.shutil.which", lambda _: None)
    m = load_manifest(example_manifest)
    j = load_journal(FRONTIERS_JOURNAL)
    drafter = ManuscriptDrafter(m, j, output_root=tmp_path / "runs")
    with pytest.raises(RuntimeError, match="pandoc is not on PATH"):
        drafter.draft_docx()
