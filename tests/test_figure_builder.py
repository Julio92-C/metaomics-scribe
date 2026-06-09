"""FigureBuilder tests — placeholder PNGs in tmp_path; opt-in real fixtures via env var.

The placeholder path stamps solid-colour PNGs at the manifest-declared sizes
and rebuilds a self-contained mini-project (manifest.json + Figures tree) in
tmp_path. This keeps CI hermetic and doesn't commit large binaries to the repo.

The opt-in path reads `METAOMICS_REAL_FIXTURES` (a directory containing a real
chicken_batch2 project), skips if unset, and exercises the same builder against
real pipeline outputs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from PIL import Image

from metaomics_scribe.figure_builder import FigureBuilder
from metaomics_scribe.journal import load_journal
from metaomics_scribe.manifest import load_manifest

REPO = Path(__file__).parent.parent
EXAMPLE_MANIFEST = REPO / "examples" / "manifest.example.json"
FRONTIERS_JOURNAL = REPO / "journals" / "frontiers_microbiome.yaml"

REAL_FIXTURES_ENV = "METAOMICS_REAL_FIXTURES"


# ---------------------------------------------------------------------------
# placeholder fixture helpers
# ---------------------------------------------------------------------------


def _stamp_png(path: Path, width: int, height: int, colour: tuple[int, int, int]) -> None:
    """Write a solid-colour RGB PNG at (width, height) to `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (width, height), colour).save(path, format="PNG")


def _materialise_example(tmp_path: Path) -> Path:
    """Copy the example manifest into tmp_path and stamp placeholder PNGs for
    every figure it references. Returns the path of the manifest inside the
    materialised project.

    Layout mirrors what the example manifest expects:
        <tmp_path>/test_run/Figures/...           (referenced PNGs)
        <tmp_path>/project/run/manifest.json      (project_root walks up "../..")
    """
    data = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    manifest_dir = tmp_path / "project" / "run"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

    palette = [
        (220, 80, 80),
        (80, 180, 120),
        (80, 130, 220),
        (220, 180, 80),
        (180, 100, 220),
        (100, 200, 200),
        (200, 200, 100),
        (140, 140, 140),
        (240, 120, 160),
    ]
    pi = 0
    for stage in data["stages"].values():
        for fig in stage.get("figures", []):
            # Only stamp PNGs — leave non-PNG (e.g., the top_candidates pdf) alone
            # because v0.2 only composes PNGs.
            if not fig["path"].lower().endswith(".png"):
                continue
            target = tmp_path / fig["path"]
            _stamp_png(
                target,
                fig.get("width_px", 1200),
                fig.get("height_px", 900),
                palette[pi % len(palette)],
            )
            pi += 1
    return manifest_dir / "manifest.json"


@pytest.fixture
def example_project(tmp_path: Path) -> Path:
    return _materialise_example(tmp_path)


# ---------------------------------------------------------------------------
# placeholder-fixture tests (always run)
# ---------------------------------------------------------------------------


def test_resolve_panels_fig2_finds_all_three(example_project: Path, tmp_path: Path):
    m = load_manifest(example_project)
    j = load_journal(FRONTIERS_JOURNAL)
    fb = FigureBuilder(m, j, output_root=tmp_path / "runs")

    resolved = fb.resolve_panels("fig2_alpha_beta")
    kinds = [(r.figure.kind, r.figure.metric) for r in resolved]
    assert kinds == [
        ("alpha_violin", "Shannon diversity index"),
        ("alpha_violin", "Richness"),
        ("pcoa_scatter", None),
    ]


def test_build_fig2_writes_png_and_caption(example_project: Path, tmp_path: Path):
    m = load_manifest(example_project)
    j = load_journal(FRONTIERS_JOURNAL)
    fb = FigureBuilder(m, j, output_root=tmp_path / "runs")

    out = fb.build("fig2_alpha_beta")
    assert out.exists()
    assert out.name == "fig2_alpha_beta.png"
    assert out.parent.name == "figures"
    assert out.parent.parent.name == "chicken_batch2"

    # Sized to the journal's double-column width x max height at dpi_min.
    # frontiers_microbiome.yaml: double=180mm, max_height=235mm, dpi_min=300.
    with Image.open(out) as img:
        assert img.width == int(180 / 25.4 * 300)
        assert img.height == int(235 / 25.4 * 300)

    caption = (out.parent / "fig2_alpha_beta.caption.txt").read_text(encoding="utf-8")
    assert "(A)" in caption and "(B)" in caption and "(C)" in caption
    assert "Shannon" in caption


def test_missing_kind_reflows(example_project: Path, tmp_path: Path):
    """Drop one panel of fig2 (three panels total) and confirm the slot still
    builds from the surviving two."""
    pcoa = (
        example_project.parent.parent.parent
        / "test_run"
        / "Figures"
        / "beta_diversity"
        / "pcoa.png"
    )
    assert pcoa.exists()
    pcoa.unlink()

    m = load_manifest(example_project)
    j = load_journal(FRONTIERS_JOURNAL)
    fb = FigureBuilder(m, j, output_root=tmp_path / "runs")

    resolved = fb.resolve_panels("fig2_alpha_beta")
    assert [r.figure.kind for r in resolved] == ["alpha_violin", "alpha_violin"]

    out = fb.build("fig2_alpha_beta")
    caption = (out.parent / "fig2_alpha_beta.caption.txt").read_text(encoding="utf-8")
    assert "(A)" in caption and "(B)" in caption
    assert "(C)" not in caption


def test_all_panels_missing_raises(example_project: Path, tmp_path: Path):
    """fig3_daa points at a volcano PNG and a top_candidates PDF. Deleting the
    PNG leaves nothing the PNG-only v0.2 builder can compose, so it must refuse."""
    volcano = (
        example_project.parent.parent.parent
        / "test_run"
        / "Figures"
        / "differential_abundance"
        / "taxa"
        / "volcano_Control_W4_vs_Dulse_W4.png"
    )
    volcano.unlink()

    m = load_manifest(example_project)
    j = load_journal(FRONTIERS_JOURNAL)
    fb = FigureBuilder(m, j, output_root=tmp_path / "runs")

    assert fb.resolve_panels("fig3_daa") == []
    with pytest.raises(RuntimeError, match="no resolvable panels"):
        fb.build("fig3_daa")


def test_single_panel_slot(example_project: Path, tmp_path: Path):
    """fig1_overview has a single panel — exercises the 1x1 layout path."""
    m = load_manifest(example_project)
    j = load_journal(FRONTIERS_JOURNAL)
    fb = FigureBuilder(m, j, output_root=tmp_path / "runs")

    out = fb.build("fig1_overview")
    with Image.open(out) as img:
        assert img.size == (int(180 / 25.4 * 300), int(235 / 25.4 * 300))


def test_unknown_slot_raises(example_project: Path, tmp_path: Path):
    m = load_manifest(example_project)
    j = load_journal(FRONTIERS_JOURNAL)
    fb = FigureBuilder(m, j, output_root=tmp_path / "runs")
    with pytest.raises(KeyError, match="not found"):
        fb.build("fig99_does_not_exist")


# ---------------------------------------------------------------------------
# opt-in real-PNG test
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    REAL_FIXTURES_ENV not in os.environ,
    reason=f"set {REAL_FIXTURES_ENV}=<path to a real chicken_batch2 project> to enable",
)
def test_real_chicken_batch2(tmp_path: Path):
    project_root = Path(os.environ[REAL_FIXTURES_ENV])
    manifest_path = project_root / "manifest.json"
    if not manifest_path.exists():
        # Allow pointing at the directory that *contains* the manifest a few levels up.
        candidates = list(project_root.rglob("manifest.json"))
        assert candidates, f"no manifest.json under {project_root}"
        manifest_path = candidates[0]

    m = load_manifest(manifest_path)
    j = load_journal(FRONTIERS_JOURNAL)
    fb = FigureBuilder(m, j, output_root=tmp_path / "runs")

    out = fb.build("fig2_alpha_beta")
    assert out.exists()
    with Image.open(out) as img:
        assert img.width > 0 and img.height > 0
