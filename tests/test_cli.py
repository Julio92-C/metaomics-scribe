"""Tests for the ``metaomics-scribe`` console script.

The deterministic path is exercised directly. The ``--llm`` branch is covered
by monkey-patching :func:`metaomics_scribe.llm.default_caller` to return a
recording stub, so the test stays offline regardless of whether
``claude-agent-sdk`` is installed.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from metaomics_scribe.cli import main

REPO = Path(__file__).parent.parent
EXAMPLE_MANIFEST = REPO / "examples" / "manifest.example.json"


@pytest.fixture
def example_manifest(tmp_path: Path) -> Path:
    """Materialise the example manifest AND every file FigureBuilder would copy.

    The CLI's draft flow stages panel composites and supplementary tables
    first (via FigureBuilder), so every panel_composite TIFF and every
    panels-stage table referenced by the manifest must exist on disk for
    the CLI invocation to succeed.
    """
    data = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    manifest_dir = tmp_path / "project" / "run"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

    panels_stage = data.get("stages", {}).get("panels", {})
    for fig in panels_stage.get("figures", []):
        if fig.get("kind") != "panel_composite":
            continue
        target = tmp_path / fig["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (200, 150), (180, 180, 180)).save(target, format="TIFF")
    for tbl in panels_stage.get("tables", []):
        target = tmp_path / tbl["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")

    return manifest_dir / "manifest.json"


def test_draft_stages_figures_and_tables(example_manifest: Path, tmp_path: Path):
    """`draft` copies every panel_composite to runs/<study>/figures/ (with caption
    sidecar) and every panels-stage table to runs/<study>/tables/ before
    rendering the manuscript — matching what batch 1 produced manually."""
    output_root = tmp_path / "runs"
    rc = main(
        [
            "draft",
            str(example_manifest),
            "--output-root",
            str(output_root),
            "--no-methods",
        ]
    )
    assert rc == 0

    figures_dir = output_root / "chicken_batch2" / "figures"
    assert (figures_dir / "fig01_taxa_overview.tiff").exists()
    assert (figures_dir / "fig01_taxa_overview.caption.txt").exists()
    assert (figures_dir / "fig05_relative_abundance_species.tiff").exists()
    assert (figures_dir / "figS00_heatmap_species.tiff").exists()

    tables_dir = output_root / "chicken_batch2" / "tables"
    assert (tables_dir / "supplementary_tables.xlsx").exists()


def test_draft_writes_markdown_without_llm(example_manifest: Path, tmp_path: Path):
    output_root = tmp_path / "runs"
    rc = main(
        [
            "draft",
            str(example_manifest),
            "--output-root",
            str(output_root),
            "--no-methods",
        ]
    )
    assert rc == 0
    md = output_root / "chicken_batch2" / "manuscript.md"
    assert md.exists()
    text = md.read_text(encoding="utf-8")
    assert "## Materials and Methods" in text
    # Without --llm, the Results stub still ships.
    assert "TODO: prose for community_overview" in text


def test_draft_with_llm_uses_patched_caller(
    example_manifest: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """--llm goes through llm.default_caller(); patching it bypasses the SDK."""
    prose = (
        "Community-level diversity in `fig01_taxa_overview` and composition in "
        "`fig05_relative_abundance_species` were assessed across 32 samples."
    )

    def _fake_default_caller():
        def _call(system: str, user: str, model: str) -> str:
            return prose

        return _call

    monkeypatch.setattr("metaomics_scribe.manuscript.default_caller", _fake_default_caller)

    output_root = tmp_path / "runs"
    rc = main(
        [
            "draft",
            str(example_manifest),
            "--output-root",
            str(output_root),
            "--no-methods",
            "--llm",
        ]
    )
    assert rc == 0
    text = (output_root / "chicken_batch2" / "manuscript.md").read_text(encoding="utf-8")
    assert prose in text


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not on PATH")
def test_draft_with_llm_and_docx_preserves_llm_prose(
    example_manifest: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`--llm --docx` together must not clobber the LLM-drafted markdown.

    Regression: ``draft_docx()`` internally calls ``self.draft()`` to produce
    the markdown for pandoc. When the CLI ran ``draft_with_llm()`` first, the
    ``finally`` block had already cleared ``_llm_prose``, so the second render
    emitted stub Results and overwrote the good markdown before pandoc ran.
    """
    prose = (
        "Community-level diversity in `fig01_taxa_overview` and composition in "
        "`fig05_relative_abundance_species` were assessed across 32 samples."
    )

    def _fake_default_caller():
        def _call(system: str, user: str, model: str) -> str:
            return prose

        return _call

    monkeypatch.setattr("metaomics_scribe.manuscript.default_caller", _fake_default_caller)

    output_root = tmp_path / "runs"
    rc = main(
        [
            "draft",
            str(example_manifest),
            "--output-root",
            str(output_root),
            "--no-methods",
            "--llm",
            "--docx",
        ]
    )
    assert rc == 0
    md = (output_root / "chicken_batch2" / "manuscript.md").read_text(encoding="utf-8")
    assert prose in md
    docx = output_root / "chicken_batch2" / "manuscript.docx"
    assert docx.exists()
    assert docx.read_bytes()[:2] == b"PK"


def test_draft_with_llm_fails_clearly_when_sdk_missing(
    example_manifest: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    """If `claude-agent-sdk` isn't installed, --llm exits 2 with an actionable message."""

    def _missing_sdk_caller():
        raise RuntimeError(
            "claude-agent-sdk is not installed. Install the optional `llm` "
            "extra with: uv sync --extra llm"
        )

    monkeypatch.setattr("metaomics_scribe.manuscript.default_caller", _missing_sdk_caller)

    rc = main(
        [
            "draft",
            str(example_manifest),
            "--output-root",
            str(tmp_path / "runs"),
            "--no-methods",
            "--llm",
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "uv sync --extra llm" in err
