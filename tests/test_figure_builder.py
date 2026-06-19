"""FigureBuilder tests — router-mode, study-agnostic journal.

The pipeline ships pre-stitched manuscript composites via a ``panels`` stage
in the manifest. The journal template is study-agnostic: it carries journal
style + IMRaD structure only, never an enumeration of figure slot ids. The
router routes every ``panel_composite`` the manifest declares and groups
them by the pipeline-emitted ``subsection`` field.

An opt-in test reads ``METAOMICS_REAL_FIXTURES`` (the directory containing a
real pipeline project) and exercises the router against real output.
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


def _minimal_journal(tmp_path: Path) -> Path:
    """Write a study-agnostic journal YAML for tests.

    No slot lists, no per-subsection slot pins — matches the current
    ``Journal`` model. Slots are discovered from the manifest at build time.
    """
    data = {
        "journal": {"id": "test_journal", "name": "Test"},
        "manuscript": {
            "citation_style": "frontiers",
            "sections": [
                {
                    "id": "results",
                    "subsections": [
                        {"id": "community_overview", "title": "Community"},
                        {"id": "resistome", "title": "Resistome"},
                    ],
                }
            ],
        },
        "figures": {
            "column_widths_mm": {"single": 85, "double": 180},
            "max_height_mm": 235,
            "dpi_min": 300,
            "preferred_format": "tiff",
        },
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


def test_build_routes_one_composite(example_project: Path, tmp_path: Path):
    """build() copies a single composite byte-for-byte and writes the caption."""
    m = load_manifest(example_project)
    fb = FigureBuilder(m, load_journal(_minimal_journal(tmp_path)),
                      output_root=tmp_path / "runs")

    built = fb.build("fig01_taxa_overview")

    assert built.out_path.exists()
    assert built.out_path.name == "fig01_taxa_overview.tiff"
    assert built.out_path.parent.name == "figures"
    assert built.out_path.parent.parent.name == "chicken_batch2"
    assert built.section == "main"
    assert built.subsection == "community_overview"
    assert _file_sha256(built.source_path) == _file_sha256(built.out_path)

    caption = built.caption_path.read_text(encoding="utf-8")
    assert "Taxonomic overview" in caption
    # No journal-side title prefix anymore — caption is the manifest seed only.
    assert "Title for" not in caption


def test_build_all_routes_every_composite(example_project: Path, tmp_path: Path):
    """build_all() returns one BuiltFigure per panel_composite, in pipeline order."""
    m = load_manifest(example_project)
    fb = FigureBuilder(m, load_journal(_minimal_journal(tmp_path)),
                      output_root=tmp_path / "runs")

    built = fb.build_all()
    assert [bf.slot_id for bf in built] == m.panel_slot_ids()
    assert all(bf.out_path.exists() for bf in built)


def test_group_by_subsection_buckets_correctly(example_project: Path, tmp_path: Path):
    """The example manifest has two community_overview composites and one
    supplementary figure with no subsection — group_by_subsection must bucket
    those as community_overview=2 and None=1."""
    m = load_manifest(example_project)
    fb = FigureBuilder(m, load_journal(_minimal_journal(tmp_path)),
                      output_root=tmp_path / "runs")

    groups = FigureBuilder.group_by_subsection(fb.build_all())
    assert sorted(groups.keys(), key=lambda x: (x is None, x)) == ["community_overview", None]
    assert len(groups["community_overview"]) == 2
    assert len(groups[None]) == 1
    assert groups[None][0].section == "supplementary"


def test_build_preserves_png_extension(tmp_path: Path):
    """A panel emitted as PNG stays PNG — no silent re-encoding."""
    data = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    manifest_dir = tmp_path / "project" / "run"
    manifest_dir.mkdir(parents=True)
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
                "subsection": "resistome",
                "slot": "fig_demo",
                "caption_seed": "Demo PNG",
            }
        ],
    }
    (manifest_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / png_rel).parent.mkdir(parents=True)
    Image.new("RGB", (800, 600), (255, 0, 0)).save(tmp_path / png_rel, format="PNG")

    fb = FigureBuilder(load_manifest(manifest_dir / "manifest.json"),
                      load_journal(_minimal_journal(tmp_path)),
                      output_root=tmp_path / "runs")

    built = fb.build("fig_demo")
    assert built.out_path.suffix == ".png"
    with Image.open(built.out_path) as img:
        assert img.format == "PNG"


def test_unknown_slot_raises(example_project: Path, tmp_path: Path):
    """Asking for a slot that isn't in the manifest raises clearly."""
    m = load_manifest(example_project)
    fb = FigureBuilder(m, load_journal(_minimal_journal(tmp_path)),
                      output_root=tmp_path / "runs")

    with pytest.raises(RuntimeError, match="no entry in the manifest `panels` stage"):
        fb.build("fig_does_not_exist")


def test_panel_listed_but_file_missing_on_disk(example_project: Path, tmp_path: Path):
    """Pipeline declared the composite but the file was deleted/renamed since."""
    m = load_manifest(example_project)
    panel = m.find_panel("fig01_taxa_overview")
    assert panel is not None
    m.resolve_path(panel.path).unlink()

    fb = FigureBuilder(m, load_journal(_minimal_journal(tmp_path)),
                      output_root=tmp_path / "runs")

    with pytest.raises(RuntimeError, match="missing on disk"):
        fb.build("fig01_taxa_overview")


def test_manifest_without_panels_stage_raises(tmp_path: Path):
    """An older manifest (no `panels` stage) cannot be routed — surface clearly."""
    data = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    data["stages"].pop("panels", None)
    manifest_dir = tmp_path / "project" / "run"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

    fb = FigureBuilder(load_manifest(manifest_dir / "manifest.json"),
                      load_journal(_minimal_journal(tmp_path)),
                      output_root=tmp_path / "runs")

    with pytest.raises(RuntimeError, match="no entry in the manifest `panels` stage"):
        fb.build("fig01_taxa_overview")


def test_frontiers_journal_has_no_slot_lists():
    """The shipped Frontiers YAML must not enumerate slot ids — the template
    is study-agnostic; slots live in the manifest."""
    text = FRONTIERS_JOURNAL.read_text(encoding="utf-8")
    assert "figure_slots:" not in text
    assert "supplementary_slots:" not in text
    assert "slots:" not in text  # also rules out subsection.slots pins

    # And the model has no such attributes.
    j = load_journal(FRONTIERS_JOURNAL)
    assert not hasattr(j, "figure_slots")
    assert not hasattr(j, "supplementary_slots")


def test_frontiers_journal_subsections_are_abstract():
    """Frontiers results subsections are topic names, not figure pins."""
    j = load_journal(FRONTIERS_JOURNAL)
    results = next(s for s in j.manuscript.sections if s.id == "results")
    assert results.subsections is not None
    sub_ids = {s.id for s in results.subsections}
    assert {"resistome", "virulome", "mobilome"} <= sub_ids


# ---------------------------------------------------------------------------
# supplementary tables routing
# ---------------------------------------------------------------------------


def test_supplementary_tables_routed(example_project: Path, tmp_path: Path):
    """build_supplementary_tables() copies the multi-sheet xlsx to runs/<study>/tables/."""
    xlsx_rel = "test_run/Datasets/panels/supplementary_tables.xlsx"
    src = tmp_path / xlsx_rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"PK\x03\x04 fake-xlsx-bytes-for-byte-identity-check")

    m = load_manifest(example_project)
    fb = FigureBuilder(m, load_journal(_minimal_journal(tmp_path)),
                      output_root=tmp_path / "runs")

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

    fb = FigureBuilder(load_manifest(manifest_dir / "manifest.json"),
                      load_journal(_minimal_journal(tmp_path)),
                      output_root=tmp_path / "runs")

    assert fb.build_supplementary_tables() is None


def test_supplementary_tables_missing_on_disk_raises(example_project: Path, tmp_path: Path):
    """Declared in manifest but file missing — fail loudly."""
    m = load_manifest(example_project)
    fb = FigureBuilder(m, load_journal(_minimal_journal(tmp_path)),
                      output_root=tmp_path / "runs")

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

    available = m.panel_slot_ids()
    assert available, "real manifest must include a `panels` stage"

    built = fb.build_all()
    assert len(built) == len(available)
    assert all(bf.out_path.exists() and bf.out_path.stat().st_size > 0 for bf in built)
