"""Figure router.

The upstream pipeline composes every multi-panel manuscript figure itself and
emits the resulting TIFFs/PNGs through a dedicated ``panels`` stage. Each
composite Figure carries ``kind: "panel_composite"`` with the manuscript
``slot`` id and ``section`` (``main`` / ``supplementary``).

The agent's job here is therefore narrow: for each slot id declared by the
journal template, look the composite up in the manifest, copy the file
verbatim to ``runs/<study_id>/figures/<slot_id>.<ext>``, and write a
``<slot_id>.caption.txt`` sidecar combining the journal-side title with the
manifest-side ``caption_seed``.

No stitching. No re-encoding. No Pillow layout work. If the pipeline drifts
(wrong DPI, missing file, slot id absent from the manifest) the builder raises
loudly rather than papering over it — composite drift is a contract bug, not a
soft warning.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .journal import Journal
from .manifest import SUPPLEMENTARY_TABLES_KIND, Figure, Manifest


@dataclass(frozen=True)
class BuiltFigure:
    """Result of routing one slot. Returned by ``FigureBuilder.build``."""

    slot_id: str
    out_path: Path
    caption_path: Path
    source_path: Path


@dataclass(frozen=True)
class BuiltTable:
    """Result of routing a manuscript-level table. Returned by
    ``FigureBuilder.build_supplementary_tables``."""

    kind: str
    out_path: Path
    source_path: Path


class FigureBuilder:
    """Route pre-stitched composites from the manifest into a manuscript run dir."""

    def __init__(self, manifest: Manifest, journal: Journal, output_root: str | Path = "runs"):
        self.manifest = manifest
        self.journal = journal
        self.output_root = Path(output_root)

    def _figures_dir(self) -> Path:
        return self.output_root / self.manifest.study.id / "figures"

    def _tables_dir(self) -> Path:
        return self.output_root / self.manifest.study.id / "tables"

    def _find_panel(self, slot_id: str) -> Figure:
        """Locate the manifest panel composite for ``slot_id`` or raise.

        The journal's slot list and the pipeline's ``panels`` stage share the
        same ids by contract — a mismatch usually means the pipeline hasn't
        re-emitted the manifest after a re-run, so the error message points
        the human author at that.
        """
        # Confirm the slot exists in the journal first — an unknown slot is a
        # journal-template typo, not a pipeline issue.
        self.journal.slot(slot_id)

        panel = self.manifest.find_panel(slot_id)
        if panel is None:
            available = self.manifest.panel_slot_ids()
            raise RuntimeError(
                f"slot {slot_id!r} has no entry in the manifest `panels` stage — "
                f"pipeline likely hasn't emitted the composite for it yet "
                f"(available: {available})"
            )
        return panel

    def build(self, slot_id: str) -> BuiltFigure:
        """Route the composite for ``slot_id`` and write the caption sidecar.

        Returns a ``BuiltFigure`` carrying the output path, the caption path,
        and the resolved source path (useful for downstream provenance). The
        output file extension matches the source — TIFFs stay TIFFs, PNGs stay
        PNGs. The pipeline owns format choice.
        """
        panel = self._find_panel(slot_id)
        slot = self.journal.slot(slot_id)

        src_abs = self.manifest.resolve_path(panel.path)
        if not src_abs.exists():
            raise RuntimeError(
                f"composite for slot {slot_id!r} declared in manifest but "
                f"missing on disk: {src_abs}"
            )

        ext = self._resolve_extension(panel, src_abs)
        out_dir = self._figures_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{slot_id}.{ext}"
        shutil.copyfile(src_abs, out_path)

        caption_path = out_dir / f"{slot_id}.caption.txt"
        caption_path.write_text(
            _build_caption(slot.title, panel.caption_seed), encoding="utf-8"
        )

        return BuiltFigure(
            slot_id=slot_id,
            out_path=out_path,
            caption_path=caption_path,
            source_path=src_abs,
        )

    def build_supplementary_tables(self) -> BuiltTable | None:
        """Route the pipeline's multi-sheet supplementary-tables xlsx.

        Looks for a Table with ``kind == "supplementary_tables"`` in the
        ``panels`` stage and copies it verbatim to
        ``runs/<study_id>/tables/supplementary_tables.<ext>``. Returns
        ``None`` when the pipeline didn't emit one (older runs); raises if it
        was declared but the file is missing on disk.
        """
        table = self.manifest.find_panel_table(SUPPLEMENTARY_TABLES_KIND)
        if table is None:
            return None

        src_abs = self.manifest.resolve_path(table.path)
        if not src_abs.exists():
            raise RuntimeError(
                f"supplementary tables declared in manifest but missing on disk: {src_abs}"
            )

        ext = src_abs.suffix.lstrip(".").lower() or (table.format or "").lower()
        if not ext:
            raise RuntimeError(
                f"cannot determine extension for supplementary tables — "
                f"path {table.path!r} has no suffix and no `format` field"
            )

        out_dir = self._tables_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{SUPPLEMENTARY_TABLES_KIND}.{ext}"
        shutil.copyfile(src_abs, out_path)

        return BuiltTable(kind=SUPPLEMENTARY_TABLES_KIND, out_path=out_path, source_path=src_abs)

    @staticmethod
    def _resolve_extension(panel: Figure, src_abs: Path) -> str:
        """Decide the output extension. Prefer the path suffix; fall back to ``format``."""
        suffix = src_abs.suffix.lstrip(".").lower()
        if suffix:
            return suffix
        if panel.format:
            return panel.format.lower()
        raise RuntimeError(
            f"cannot determine output extension for panel {panel.slot!r} — "
            f"path has no suffix and no `format` field"
        )


def _build_caption(title: str | None, caption_seed: str | None) -> str:
    """Join the journal title and pipeline caption_seed into a sidecar string."""
    parts = [s for s in (title, caption_seed) if s]
    if not parts:
        return ""
    return "\n\n".join(parts) + "\n"
