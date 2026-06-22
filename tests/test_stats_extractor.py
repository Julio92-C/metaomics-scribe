"""Tests for the deterministic stats_text parsers.

The parsers underpin the v0.4.1 LLM grounding pack — every numeric value the
model may quote in a Results subsection has to round-trip through here. The
fixtures use the exact text the upstream pipeline emits, captured from real
PERMANOVA/Kruskal-Wallis outputs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metaomics_scribe.manifest import load_manifest
from metaomics_scribe.stats_extractor import (
    _parse_kruskal,
    _parse_permanova,
    _parse_permdisp,
    extract_stats,
)

REPO = Path(__file__).parent.parent
EXAMPLE_MANIFEST = REPO / "examples" / "manifest.example.json"


# ---------------------------------------------------------------------------
# Per-kind parser unit tests (synthetic fixtures matching pipeline output)
# ---------------------------------------------------------------------------


KRUSKAL_TEXT = """\
Berger Parker index ~ Treatment: p_raw = 0.6639, p_adj (BH) = 0.6639
Effective number of species ~ Treatment: p_raw = 0.4285, p_adj (BH) = 0.6122
Fisher's alpha ~ Treatment: p_raw = 0.1495, p_adj (BH) = 0.3315
Shannon diversity index ~ Treatment: p_raw = 0.3805, p_adj (BH) = 0.6122
"""


def test_parse_kruskal_extracts_every_row():
    rows = _parse_kruskal(KRUSKAL_TEXT)
    assert len(rows) == 4
    shannon = next(r for r in rows if r.metric == "Shannon diversity index")
    assert shannon.grouping_variable == "Treatment"
    assert shannon.p_raw == 0.3805
    assert shannon.p_adj == 0.6122


def test_parse_kruskal_skips_garbage_lines():
    text = (
        "Garbage header line\n"
        "Shannon diversity index ~ Treatment: p_raw = 0.05, p_adj (BH) = 0.10\n"
        "\n"
        "Footer line that doesn't match\n"
    )
    rows = _parse_kruskal(text)
    assert len(rows) == 1
    assert rows[0].metric == "Shannon diversity index"


PERMANOVA_TEXT = """\
Permutation test for adonis under reduced model
Permutation: free
Number of permutations: 9999

vegan::adonis2(formula = stats::reformulate(group, "d"), data = meta_aligned)
         Df SumOfSqs      R2      F Pr(>F)
Model     2   0.6261 0.14461 1.2679 0.2193
Residual 15   3.7036 0.85539
Total    17   4.3297 1.00000
"""


def test_parse_permanova_extracts_model_row_and_perms():
    result = _parse_permanova(PERMANOVA_TEXT, source="permanova")
    assert result is not None
    assert result.source == "permanova"
    assert result.n_permutations == 9999
    assert result.df_model == 2
    assert result.df_residual == 15
    assert result.df_total == 17
    assert result.R2 == 0.14461
    assert result.F == 1.2679
    assert result.p_value == 0.2193


def test_parse_permanova_returns_none_for_unrelated_text():
    assert _parse_permanova("hello world\nnothing here", source="permanova") is None


PERMDISP_TEXT = """\
Permutation test for homogeneity of multivariate dispersions
Permutation: free
Number of permutations: 999

         Df Sum Sq Mean Sq      F N.Perm Pr(>F)
Groups    3 0.0050 0.00167 4.2100    999 0.0120
Residuals 14 0.0055 0.00039

Average distance to median:
Control_W4   0.345
Dulse_W4     0.432
"""


def test_parse_permdisp_extracts_f_p_and_group_means():
    result = _parse_permdisp(PERMDISP_TEXT, source="permdisp")
    assert result is not None
    assert result.source == "permdisp"
    assert result.n_permutations == 999
    assert result.F == 4.21
    assert result.p_value == 0.012
    assert result.group_mean_distances["Control_W4"] == 0.345
    assert result.group_mean_distances["Dulse_W4"] == 0.432


# ---------------------------------------------------------------------------
# Manifest-driven integration test
# ---------------------------------------------------------------------------


@pytest.fixture
def manifest_with_stats(tmp_path: Path):
    """Materialise the example manifest plus real stats_text files on disk."""
    data = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    manifest_dir = tmp_path / "project" / "run"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

    project_root = manifest_dir / data["outputs"]["project_root"]
    alpha_path = project_root / "test_run" / "Datasets" / "alpha_diversity" / "alpha_stats.txt"
    permanova_path = project_root / "test_run" / "Figures" / "beta_diversity" / "permanova.txt"
    permdisp_path = project_root / "test_run" / "Figures" / "beta_diversity" / "permdisp.txt"
    for p in (alpha_path, permanova_path, permdisp_path):
        p.parent.mkdir(parents=True, exist_ok=True)
    alpha_path.write_text(KRUSKAL_TEXT, encoding="utf-8")
    permanova_path.write_text(PERMANOVA_TEXT, encoding="utf-8")
    permdisp_path.write_text(PERMDISP_TEXT, encoding="utf-8")
    return manifest_dir / "manifest.json"


def test_extract_stats_populates_alpha_and_beta_stages(manifest_with_stats: Path):
    m = load_manifest(manifest_with_stats)
    bundle = extract_stats(m)
    assert "alpha_diversity" in bundle.by_stage
    assert "beta_diversity" in bundle.by_stage

    alpha = bundle.by_stage["alpha_diversity"]
    assert len(alpha.kruskal) == 4
    assert {r.metric for r in alpha.kruskal} >= {"Shannon diversity index"}

    beta = bundle.by_stage["beta_diversity"]
    assert len(beta.permanovas) == 1
    assert beta.permanovas[0].source == "permanova"
    assert beta.permanovas[0].p_value == 0.2193
    assert len(beta.permdisps) == 1
    assert beta.permdisps[0].source == "permdisp"
    assert beta.permdisps[0].p_value == 0.012


JACCARD_PERMANOVA_TEXT = """\
Permutation test for adonis under reduced model
Permutation: free
Number of permutations: 9999

vegan::adonis2(formula = stats::reformulate(group, "d"), data = meta_aligned)
         Df SumOfSqs      R2      F Pr(>F)
Model     2   0.7385 0.13383 1.1588 0.2377
Residual 15   3.7036 0.85539
Total    17   4.3297 1.00000
"""


def test_extract_stats_keeps_both_permanova_files_with_distinct_sources(tmp_path: Path):
    """Regression: chicken_batch1 declared two `kind: permanova` entries (one
    Bray-Curtis, one Jaccard). The old extractor overwrote the field with the
    second one, letting the LLM quote Jaccard numbers under a Bray-Curtis
    label. We now keep both, tagged with the source filename so the prompt
    can disambiguate."""
    data = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    # Inject a second permanova file alongside the existing one.
    data["stages"]["beta_diversity"]["stats_text"].append(
        {
            "path": "test_run/Figures/beta_diversity/jaccard_permanova.txt",
            "kind": "permanova",
            "primary_var": "Treatment_Bird",
            "description": "Jaccard PERMANOVA on presence/absence.",
        }
    )
    manifest_dir = tmp_path / "project" / "run"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

    project_root = manifest_dir / data["outputs"]["project_root"]
    bray_path = project_root / "test_run" / "Figures" / "beta_diversity" / "permanova.txt"
    jaccard_path = (
        project_root / "test_run" / "Figures" / "beta_diversity" / "jaccard_permanova.txt"
    )
    for p in (bray_path, jaccard_path):
        p.parent.mkdir(parents=True, exist_ok=True)
    bray_path.write_text(PERMANOVA_TEXT, encoding="utf-8")
    jaccard_path.write_text(JACCARD_PERMANOVA_TEXT, encoding="utf-8")

    bundle = extract_stats(load_manifest(manifest_dir / "manifest.json"))
    beta = bundle.by_stage["beta_diversity"]
    assert len(beta.permanovas) == 2
    sources = {p.source for p in beta.permanovas}
    assert sources == {"permanova", "jaccard_permanova"}
    bray = next(p for p in beta.permanovas if p.source == "permanova")
    jacc = next(p for p in beta.permanovas if p.source == "jaccard_permanova")
    assert bray.F == 1.2679 and bray.p_value == 0.2193
    assert jacc.F == 1.1588 and jacc.p_value == 0.2377


def test_extract_stats_skips_stages_with_missing_files(tmp_path: Path):
    """Manifest declares a stats file that doesn't exist on disk; we ignore it
    rather than crashing the drafter."""
    data = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    manifest_dir = tmp_path / "project" / "run"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    m = load_manifest(manifest_dir / "manifest.json")
    bundle = extract_stats(m)
    # Nothing on disk → nothing in the bundle.
    assert bundle.by_stage == {}
