"""LLM drafting tests.

The default test path passes a synchronous lambda as the caller so the suite
stays offline and dependency-free. The ``live_api`` marker hits the real
Claude Agent SDK (subscription auth via local ``claude`` CLI) for opt-in
smoke testing (``uv run pytest -m live_api``).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from metaomics_scribe.journal import load_journal
from metaomics_scribe.llm import (
    ResultsGuardrailError,
    draft_figure_caption,
    draft_results_section,
    validate_no_invented_numbers,
)
from metaomics_scribe.manifest import load_manifest
from metaomics_scribe.manuscript import ManuscriptDrafter
from metaomics_scribe.stats_extractor import StatsBundle

REPO = Path(__file__).parent.parent
EXAMPLE_MANIFEST = REPO / "examples" / "manifest.example.json"
FRONTIERS_JOURNAL = REPO / "journals" / "frontiers_microbiome.yaml"


# ---------------------------------------------------------------------------
# Caller helpers — a list of canned responses, recorded calls
# ---------------------------------------------------------------------------


class _RecordingCaller:
    """Synchronous ``(system, user, model) -> str`` caller that records args."""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, system: str, user: str, model: str) -> str:
        self.calls.append((system, user, model))
        idx = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[idx]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def example_manifest(tmp_path: Path) -> Path:
    data = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    manifest_dir = tmp_path / "project" / "run"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    return manifest_dir / "manifest.json"


# ---------------------------------------------------------------------------
# validate_no_invented_numbers — guardrail unit tests
# ---------------------------------------------------------------------------


def test_guardrail_passes_when_every_number_is_grounded():
    grounding = '{"n_samples": 32, "permanova_permutations": 9999, "alpha": 0.05}'
    prose = "We observed 32 samples and ran 9,999 permutations at alpha = 0.05."
    # The comma-grouped 9,999 normalises to 9999 which is in the grounding.
    validate_no_invented_numbers(prose, grounding, subsection_id="community_overview")


def test_guardrail_rejects_invented_number():
    grounding = '{"n_samples": 32}'
    prose = "We observed 32 samples and 47 controls."  # 47 not in grounding
    with pytest.raises(ResultsGuardrailError) as exc:
        validate_no_invented_numbers(prose, grounding, subsection_id="community_overview")
    assert exc.value.subsection_id == "community_overview"
    assert "47" in exc.value.illegal_tokens


def test_guardrail_ignores_grammar_integers_zero_and_one():
    grounding = '{"n_samples": 32}'
    prose = "One sample showed 0 reads; another carried 32 features."
    validate_no_invented_numbers(prose, grounding, subsection_id="x")  # no raise


# ---------------------------------------------------------------------------
# draft_results_section — caller lambda
# ---------------------------------------------------------------------------


def test_draft_results_section_passes_grounding_to_caller():
    m = load_manifest(EXAMPLE_MANIFEST)
    j = load_journal(FRONTIERS_JOURNAL)
    results = next(s for s in j.manuscript.sections if s.id == "results")
    community = next(sub for sub in results.subsections if sub.id == "community_overview")
    composites = [
        f for f in m.stages["panels"].figures if f.subsection == "community_overview"
    ]
    assert composites, "example manifest should carry community_overview composites"

    prose = (
        "Alpha diversity was assessed across the 4 Treatment x Time groups in "
        "`fig01_taxa_overview`. Composition is shown in "
        "`fig05_relative_abundance_species`."
    )
    caller = _RecordingCaller([prose])
    out = draft_results_section(
        caller,
        manifest=m,
        stats_bundle=StatsBundle(),
        subsection=community,
        composites=composites,
    )
    assert out == prose

    system, user, model = caller.calls[0]
    assert model == "claude-opus-4-7"
    assert "GROUNDING PACK" in system
    # Slot id of one composite must appear in the per-call user prompt.
    assert "fig01_taxa_overview" in user


def test_draft_results_section_raises_on_fabricated_number():
    m = load_manifest(EXAMPLE_MANIFEST)
    j = load_journal(FRONTIERS_JOURNAL)
    results = next(s for s in j.manuscript.sections if s.id == "results")
    community = next(sub for sub in results.subsections if sub.id == "community_overview")
    composites = [
        f for f in m.stages["panels"].figures if f.subsection == "community_overview"
    ]
    # 12345 is not anywhere in the example manifest.
    bad_prose = (
        "Across the 12345 sequencing reads in `fig01_taxa_overview`, diversity rose."
    )
    with pytest.raises(ResultsGuardrailError):
        draft_results_section(
            _RecordingCaller([bad_prose]),
            manifest=m,
            stats_bundle=StatsBundle(),
            subsection=community,
            composites=composites,
        )


# ---------------------------------------------------------------------------
# draft_figure_caption — per-composite caller
# ---------------------------------------------------------------------------


def test_draft_figure_caption_passes_grounding_and_slot_to_caller():
    m = load_manifest(EXAMPLE_MANIFEST)
    composite = next(
        f for f in m.stages["panels"].figures
        if f.kind == "panel_composite" and f.slot == "fig01_taxa_overview"
    )

    caption = (
        "The composite integrates alpha-diversity violins, beta-diversity ordination, "
        "and stacked-bar composition across the 4 Treatment x Time groups."
    )
    caller = _RecordingCaller([caption])
    out = draft_figure_caption(
        caller,
        manifest=m,
        stats_bundle=StatsBundle(),
        composite=composite,
    )
    assert out == caption

    system, user, model = caller.calls[0]
    assert model == "claude-opus-4-7"
    assert "GROUNDING PACK" in system
    # The per-call user prompt names the composite by slot id and subsection.
    assert "fig01_taxa_overview" in user
    assert "community_overview" in user


def test_draft_figure_caption_raises_on_fabricated_number():
    m = load_manifest(EXAMPLE_MANIFEST)
    composite = next(
        f for f in m.stages["panels"].figures
        if f.kind == "panel_composite" and f.slot == "fig01_taxa_overview"
    )
    bad = "Alpha diversity rose across 12345 reads in the composite."
    with pytest.raises(ResultsGuardrailError):
        draft_figure_caption(
            _RecordingCaller([bad]),
            manifest=m,
            stats_bundle=StatsBundle(),
            composite=composite,
        )


# ---------------------------------------------------------------------------
# ManuscriptDrafter.draft_with_llm — end-to-end with caller lambda
# ---------------------------------------------------------------------------


def test_draft_with_llm_renders_prose_per_subsection(
    example_manifest: Path, tmp_path: Path
):
    """`draft_with_llm` calls the caller once per subsection with composites
    AND once per composite (for the figure caption)."""
    m = load_manifest(example_manifest)
    j = load_journal(FRONTIERS_JOURNAL)
    drafter = ManuscriptDrafter(m, j, output_root=tmp_path / "runs")

    # Reusing a single grounded response keeps the guardrail happy across
    # both the prose call and every per-composite caption call.
    response = (
        "Alpha diversity in `fig01_taxa_overview` and composition in "
        "`fig05_relative_abundance_species` were assessed across 32 samples."
    )
    caller = _RecordingCaller([response])
    drafted = drafter.draft_with_llm(caller)

    text = drafted.markdown_path.read_text(encoding="utf-8")
    assert response in text
    # Subsections without composites still show the v0.4.0 stub.
    assert "no `panel_composite` carries `subsection: resistome`" in text

    # Example manifest panel composites: fig01, fig05 (community_overview) +
    # figS00 (no subsection). One prose call for community_overview, plus
    # three caption calls — one per composite.
    composite_slots = {f.slot for f in m.stages["panels"].figures if f.kind == "panel_composite"}
    assert len(composite_slots) == 3
    assert len(caller.calls) == 1 + len(composite_slots)


def test_draft_with_llm_embeds_figures_with_captions(
    example_manifest: Path, tmp_path: Path
):
    """Every panel composite gets an inline image embed plus an LLM caption."""
    m = load_manifest(example_manifest)
    j = load_journal(FRONTIERS_JOURNAL)
    drafter = ManuscriptDrafter(m, j, output_root=tmp_path / "runs")

    caption = (
        "Stacked-bar composition of the top taxa per sample across 32 samples."
    )
    caller = _RecordingCaller([caption])
    drafted = drafter.draft_with_llm(caller)

    text = drafted.markdown_path.read_text(encoding="utf-8")
    # Image embed uses a relative path so pandoc resolves to runs/<study>/figures.
    assert "![](figures/fig01_taxa_overview.tiff)" in text
    assert "![](figures/fig05_relative_abundance_species.tiff)" in text
    # Each composite has its slot id labelled and the LLM caption underneath.
    assert "**Figure `fig01_taxa_overview`.**" in text
    assert caption in text
    # Captions land in the drafter state so subsequent renders preserve them.
    assert drafter._llm_captions is not None
    assert set(drafter._llm_captions.keys()) >= {
        "fig01_taxa_overview",
        "fig05_relative_abundance_species",
        "figS00_heatmap_species",
    }


def test_draft_without_llm_embeds_figures_with_caption_seed(
    example_manifest: Path, tmp_path: Path
):
    """Deterministic draft() still embeds figures, using the manifest's
    caption_seed as the caption — so a no-LLM run produces a previewable
    docx even when caption_seed is a placeholder."""
    m = load_manifest(example_manifest)
    j = load_journal(FRONTIERS_JOURNAL)
    drafter = ManuscriptDrafter(m, j, output_root=tmp_path / "runs")

    text = drafter.draft().markdown_path.read_text(encoding="utf-8")
    assert "![](figures/fig01_taxa_overview.tiff)" in text
    assert "**Figure `fig01_taxa_overview`.**" in text
    # caption_seed for fig01 in the example manifest:
    assert "Taxonomic overview" in text


def test_draft_with_llm_retains_prose_and_captions_for_subsequent_renders(
    example_manifest: Path, tmp_path: Path
):
    """After draft_with_llm(), subsequent draft() calls reuse both the LLM
    Results prose AND every LLM figure caption.

    This is what keeps the `--llm --docx` CLI path honest: ``draft_docx()``
    internally calls ``self.draft()`` to regenerate the markdown for pandoc,
    and that second render must preserve every LLM-generated output — not
    silently revert to the deterministic stub.
    """
    response = "Community-level diversity is shown in `fig01_taxa_overview` (32 samples)."
    m = load_manifest(example_manifest)
    j = load_journal(FRONTIERS_JOURNAL)
    drafter = ManuscriptDrafter(m, j, output_root=tmp_path / "runs")
    caller = _RecordingCaller([response])

    drafter.draft_with_llm(caller)
    assert drafter._llm_prose == {"community_overview": response}
    assert drafter._llm_captions is not None
    assert "fig01_taxa_overview" in drafter._llm_captions

    second = drafter.draft().markdown_path.read_text(encoding="utf-8")
    assert response in second
    assert "TODO: prose for community_overview" not in second
    # The captions persist into the re-render too.
    assert "![](figures/fig01_taxa_overview.tiff)" in second


# ---------------------------------------------------------------------------
# Live-API smoke test (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.live_api
@pytest.mark.skipif(
    importlib.util.find_spec("claude_agent_sdk") is None,
    reason="`claude-agent-sdk` not installed; run `uv sync --extra llm` to enable",
)
def test_draft_with_llm_smoke_against_real_claude(example_manifest: Path, tmp_path: Path):
    """End-to-end against the real Claude CLI (subscription auth).

    Opt in with ``uv run pytest -m live_api``. Verifies the prompt shape we
    send is accepted by the model and the guardrail clears on a well-grounded
    draft — not the prose quality.
    """
    m = load_manifest(example_manifest)
    j = load_journal(FRONTIERS_JOURNAL)
    drafter = ManuscriptDrafter(m, j, output_root=tmp_path / "runs")
    drafted = drafter.draft_with_llm()  # default caller = claude-agent-sdk
    text = drafted.markdown_path.read_text(encoding="utf-8")
    assert "## Results" in text
    # If the model fabricated a number, draft_with_llm would have raised.
