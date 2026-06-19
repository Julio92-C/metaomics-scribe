"""FigureBuilder tests — router-mode (schema 1.3+).

The pipeline now ships pre-stitched manuscript composites and surfaces them
through the manifest's `panels` block. These tests build a self-contained mini
project (manifest.json + per-panel TIFF stubs) in tmp_path, then verify that
`FigureBuilder.build(slot_id)` routes each composite to the runs directory,
preserves the source extension, and writes the caption sidecar.

An opt-in test reads `METAOMICS_REAL_FIXTURES` (the directory containing a real
chicken_batch project) and exercises the router against real pipeline output.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
import yaml
from PIL import Image

from metaomics_scribe.figure_builder import FigureBuilder
from metaomics_scribe.journal import load_journal
from metaomics_scribe.manifest import load_manifest

REPO = Path(__file__).parent.parent
EXAMPLE_MANIFEST = REPO / "examples" / "manifest.example.json"
FRONTIERS_JOURNAL = REPO / "journals" / "frontiers_microbiome.yaml"

REAL_FIXTURES_ENV = "METAOMICS_REAL_FIXTURES"


def _stamp_tiff(path: Path, width: int, height: int, colour: tuple[int, int, int]) -> None:
    """Write a solid-colour RGB TIFF — stand-in for a pipeline composite."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (width, height), colour).save(path, format="TIFF")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _materialise_example(tmp_path: Path) -> Path:
    """Drop the example manifest into tmp_path and stamp the composites it lists.

    Layout (matches what the example manifest expects):
        <tmp_path>/test_run/Figures/panels/{main,supplementary}/*.tiff
        <tmp_path>/project/run/manifest.json   (project_root walks up ../..)
    """
    data = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    manifest_dir = tmp_path / "project" / "run"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

    palette = [(220, 80, 80), (80, 180, 120), (80, 130, 220), (220, 180, 80)]
    panel_figs = [
        f for f in data.get("stages", {}).get("panels", {}).get("figures", [])
        if f.get("kind") == "panel_composite"
    ]
    for i, fig in enumerate(panel_figs):
        target = tmp_path / fig["path"]
        _stamp_tiff(target, 1800, 1200, palette[i % len(palette)])
    return manifest_dir / "manifest.json"


def _minimal_journal(
    tmp_path: Path, slot_ids_main: list[str], slot_ids_supp: list[str] = ()
) -> Path:
    """Write a stripped-down journal YAML so tests don't depend on the Frontiers
    file's exact slot list. Slots have id + title only."""
    data = {
        "journal": {"id": "test_journal", "name": "Test"},
        "manuscript": {"citation_style": "frontiers", "sections": [{"id": "results"}]},
        "figures": {
            "column_widths_mm": {"single": 85, "double": 180},
            "max_height_mm": 235,
            "dpi_min": 300,
            "preferred_format": "tiff",
        },
        "figure_slots": [{"id": sid, "title": f"Title for {sid}"} for sid in slot_ids_main],
        "supplementary_slots": [
            {"id": sid, "title": f"Supp title for {sid}"} for sid in slot_ids_supp
        ],
    }
    path = tmp_path / "test_journal.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.fixture
def example_project(tmp_path: Path) -> Path:
    return _materialise_example(tmp_path)


# ---------------------------------------------------------------------------
# router behaviour
# ---------------------------------------------------------------------------


def test_build_main_routes_composite(example_project: Path, tmp_path: Path):
    """build() for a main slot copies the manifest-listed composite verbatim
    and writes the caption sidecar."""
    m = load_manifest(example_project)
    j_path = _minimal_journal(
        tmp_path,
        slot_ids_main=["fig01_taxa_overview", "fig05_relative_abundance_species"],
    )
    j = load_journal(j_path)
    fb = FigureBuilder(m, j, output_root=tmp_path / "runs")

    built = fb.build("fig01_taxa_overview")

    assert built.out_path.exists()
    assert built.out_path.name == "fig01_taxa_overview.tiff"
    assert built.out_path.parent.name == "figures"
    assert built.out_path.parent.parent.name == "chicken_batch2"

    # Source and output must be byte-identical — no re-encoding.
    assert _file_sha256(built.source_path) == _file_sha256(built.out_path)

    caption = built.caption_path.read_text(encoding="utf-8")
    assert "Title for fig01_taxa_overview" in caption
    assert "Taxonomic overview" in caption  # caption_seed text from example manifest


def test_build_supplementary_routes_composite(example_project: Path, tmp_path: Path):
    m = load_manifest(example_project)
    j_path = _minimal_journal(
        tmp_path, slot_ids_main=[], slot_ids_supp=["figS00_heatmap_species"]
    )
    j = load_journal(j_path)
    fb = FigureBuilder(m, j, output_root=tmp_path / "runs")

    built = fb.build("figS00_heatmap_species")
    assert built.out_path.exists()
    assert built.out_path.name == "figS00_heatmap_species.tiff"
    assert "Per-sample species-level abundance heatmap" in built.caption_path.read_text(
        encoding="utf-8"
    )


def test_build_preserves_png_extension(tmp_path: Path):
    """A panel emitted as PNG stays PNG — no silent re-encoding."""
    data = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    manifest_dir = tmp_path / "project" / "run"
    manifest_dir.mkdir(parents=True)
    # Replace the example's panels stage with a single PNG-format composite.
    png_rel = "test_run/Figures/panels/main/fig_demo.png"
    data["stages"]["panels"] = {
        "status": "complete",
        "tables": [],
        "figures": [
            {
                "path": png_rel,
                "kind": "panel_composite",
                "format": "png",
                "section": "main",
                "slot": "fig_demo",
                "caption_seed": "Demo PNG",
            }
        ],
    }
    (manifest_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / png_rel).parent.mkdir(parents=True)
    Image.new("RGB", (800, 600), (255, 0, 0)).save(tmp_path / png_rel, format="PNG")

    j_path = _minimal_journal(tmp_path, slot_ids_main=["fig_demo"])
    fb = FigureBuilder(load_manifest(manifest_dir / "manifest.json"), load_journal(j_path),
                      output_root=tmp_path / "runs")

    built = fb.build("fig_demo")
    assert built.out_path.suffix == ".png"
    with Image.open(built.out_path) as img:
        assert img.format == "PNG"


def test_unknown_slot_raises(example_project: Path, tmp_path: Path):
    m = load_manifest(example_project)
    j_path = _minimal_journal(tmp_path, slot_ids_main=["fig01_taxa_overview"])
    j = load_journal(j_path)
    fb = FigureBuilder(m, j, output_root=tmp_path / "runs")

    with pytest.raises(KeyError, match="not found"):
        fb.build("fig99_does_not_exist")


def test_slot_in_journal_but_missing_from_manifest_panels(example_project: Path, tmp_path: Path):
    """A journal slot that has no composite in the `panels` stage must raise a
    clear error — silent skip would let an empty figure ship to the manuscript."""
    m = load_manifest(example_project)
    j_path = _minimal_journal(
        tmp_path,
        slot_ids_main=["fig01_taxa_overview", "fig07_sankey_vf"],  # second isn't emitted
    )
    j = load_journal(j_path)
    fb = FigureBuilder(m, j, output_root=tmp_path / "runs")

    with pytest.raises(RuntimeError, match="no entry in the manifest `panels` stage"):
        fb.build("fig07_sankey_vf")


def test_panel_listed_but_file_missing_on_disk(example_project: Path, tmp_path: Path):
    """Pipeline declared the composite but the file was deleted/renamed since."""
    m = load_manifest(example_project)
    panel = m.find_panel("fig01_taxa_overview")
    assert panel is not None
    m.resolve_path(panel.path).unlink()

    j_path = _minimal_journal(tmp_path, slot_ids_main=["fig01_taxa_overview"])
    j = load_journal(j_path)
    fb = FigureBuilder(m, j, output_root=tmp_path / "runs")

    with pytest.raises(RuntimeError, match="missing on disk"):
        fb.build("fig01_taxa_overview")


def test_caption_handles_missing_seed(tmp_path: Path):
    """If the pipeline omitted caption_seed, the sidecar still gets the title."""
    data = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    manifest_dir = tmp_path / "project" / "run"
    manifest_dir.mkdir(parents=True)
    rel = "test_run/Figures/panels/main/fig_noseed.tiff"
    data["stages"]["panels"] = {
        "status": "complete",
        "tables": [],
        "figures": [
            {
                "path": rel,
                "kind": "panel_composite",
                "format": "tiff",
                "section": "main",
                "slot": "fig_noseed",
            }
        ],
    }
    (manifest_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    _stamp_tiff(tmp_path / rel, 400, 300, (0, 0, 0))

    j_path = _minimal_journal(tmp_path, slot_ids_main=["fig_noseed"])
    fb = FigureBuilder(load_manifest(manifest_dir / "manifest.json"), load_journal(j_path),
                      output_root=tmp_path / "runs")

    built = fb.build("fig_noseed")
    caption = built.caption_path.read_text(encoding="utf-8")
    assert "Title for fig_noseed" in caption
    assert caption.strip()  # not empty


def test_manifest_without_panels_stage_raises(tmp_path: Path):
    """An older manifest (no `panels` stage) cannot be routed — surface clearly
    rather than crashing on attribute access."""
    data = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    data["stages"].pop("panels", None)
    manifest_dir = tmp_path / "project" / "run"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

    j_path = _minimal_journal(tmp_path, slot_ids_main=["fig01_taxa_overview"])
    fb = FigureBuilder(load_manifest(manifest_dir / "manifest.json"), load_journal(j_path),
                      output_root=tmp_path / "runs")

    with pytest.raises(RuntimeError, match="no entry in the manifest `panels` stage"):
        fb.build("fig01_taxa_overview")


def test_frontiers_journal_loads(example_project: Path, tmp_path: Path):
    """Smoke test: the shipped Frontiers YAML parses against the slimmed
    FigureSlot model and exposes the pipeline-named slot ids."""
    j = load_journal(FRONTIERS_JOURNAL)
    main_ids = [s.id for s in j.figure_slots]
    assert "fig01_taxa_overview" in main_ids
    assert "fig10_vf_arg_correlation" in main_ids
    assert "fig08" not in "".join(main_ids)  # fig08 intentionally gapped

    supp_ids = [s.id for s in j.supplementary_slots]
    assert "figS00_heatmap_species" in supp_ids


def test_results_subsections_pin_every_main_slot():
    """Every main figure slot the journal declares must be pinned to exactly
    one results subsection — otherwise the drafter can't decide where to write
    the figure's prose."""
    j = load_journal(FRONTIERS_JOURNAL)
    results = next(s for s in j.manuscript.sections if s.id == "results")
    assert results.subsections is not None

    pinned: dict[str, str] = {}
    for sub in results.subsections:
        for slot_id in sub.slots or []:
            assert slot_id not in pinned, (
                f"slot {slot_id!r} is pinned to both {pinned[slot_id]!r} "
                f"and {sub.id!r} — must be exactly one subsection"
            )
            pinned[slot_id] = sub.id

    main_slot_ids = {s.id for s in j.figure_slots}
    unpinned = main_slot_ids - pinned.keys()
    assert not unpinned, f"main slots not pinned to any subsection: {sorted(unpinned)}"


# ---------------------------------------------------------------------------
# supplementary tables routing
# ---------------------------------------------------------------------------


def test_supplementary_tables_routed(example_project: Path, tmp_path: Path):
    """build_supplementary_tables() copies the multi-sheet xlsx to runs/<study>/tables/."""
    # Stamp a stand-in xlsx that the example manifest declares.
    xlsx_rel = "test_run/Datasets/panels/supplementary_tables.xlsx"
    src = tmp_path / xlsx_rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"PK\x03\x04 fake-xlsx-bytes-for-byte-identity-check")

    m = load_manifest(example_project)
    j_path = _minimal_journal(tmp_path, slot_ids_main=[])
    fb = FigureBuilder(m, load_journal(j_path), output_root=tmp_path / "runs")

    built = fb.build_supplementary_tables()
    assert built is not None
    assert built.out_path.name == "supplementary_tables.xlsx"
    assert built.out_path.parent.name == "tables"
    assert built.out_path.parent.parent.name == "chicken_batch2"
    assert _file_sha256(built.source_path) == _file_sha256(built.out_path)


def test_supplementary_tables_returns_none_when_not_emitted(tmp_path: Path):
    """A manifest without a supplementary_tables Table just returns None."""
    data = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    data["stages"]["panels"]["tables"] = []
    manifest_dir = tmp_path / "project" / "run"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

    j_path = _minimal_journal(tmp_path, slot_ids_main=[])
    fb = FigureBuilder(load_manifest(manifest_dir / "manifest.json"), load_journal(j_path),
                      output_root=tmp_path / "runs")

    assert fb.build_supplementary_tables() is None


def test_supplementary_tables_missing_on_disk_raises(example_project: Path, tmp_path: Path):
    """Declared in manifest but file missing — fail loudly."""
    m = load_manifest(example_project)
    j_path = _minimal_journal(tmp_path, slot_ids_main=[])
    fb = FigureBuilder(m, load_journal(j_path), output_root=tmp_path / "runs")

    with pytest.raises(RuntimeError, match="missing on disk"):
        fb.build_supplementary_tables()


# ---------------------------------------------------------------------------
# opt-in real-fixture test
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    REAL_FIXTURES_ENV not in os.environ,
    reason=f"set {REAL_FIXTURES_ENV}=<path to a real chicken project> to enable",
)
def test_real_chicken_batch(tmp_path: Path):
    project_root = Path(os.environ[REAL_FIXTURES_ENV])
    manifest_path = project_root / "manifest.json"
    if not manifest_path.exists():
        candidates = list(project_root.rglob("manifest.json"))
        assert candidates, f"no manifest.json under {project_root}"
        manifest_path = candidates[0]

    m = load_manifest(manifest_path)
    j = load_journal(FRONTIERS_JOURNAL)
    fb = FigureBuilder(m, j, output_root=tmp_path / "runs")

    # Route the first main slot the manifest actually lists.
    available = m.panel_slot_ids()
    assert available, "real manifest must include a `panels` stage"
    target = next((s.id for s in j.figure_slots if s.id in available), None)
    assert target, "no overlap between journal slots and manifest panels"

    built = fb.build(target)
    assert built.out_path.exists()
    assert built.out_path.stat().st_size > 0
