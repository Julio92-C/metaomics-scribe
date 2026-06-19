"""Figure router.

The upstream pipeline now composes every multi-panel manuscript figure itself
and lists the resulting TIFFs/PNGs in the manifest's ``panels`` block (schema
1.3+). The agent's job here is therefore narrow: for each manuscript slot id
declared by the journal template, look the composite up in the manifest, copy
the file verbatim to ``runs/<study_id>/figures/<slot_id>.<ext>``, and write a
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
from .manifest import Manifest, Panel


@dataclass(frozen=True)
class BuiltFigure:
    """Result of routing one slot. Returned by ``FigureBuilder.build``."""

    slot_id: str
    out_path: Path
    caption_path: Path
    source_path: Path


class FigureBuilder:
    """Route pre-stitched composites from the manifest into a manuscript run dir."""

    def __init__(self, manifest: Manifest, journal: Journal, output_root: str | Path = "runs"):
        self.manifest = manifest
        self.journal = journal
        self.output_root = Path(output_root)

    def _figures_dir(self) -> Path:
        return self.output_root / self.manifest.study.id / "figures"

    def _find_panel(self, slot_id: str) -> Panel:
        """Locate the manifest panel for ``slot_id`` or raise a clear error.

        The journal's slot list and the manifest's ``panels`` block share the
        same ids by contract — a mismatch usually means the pipeline hasn't
        re-emitted the manifest after a re-run, so the error message points
        the human author at that.
        """
        # Confirm the slot exists in the journal first — an unknown slot is a
        # journal-template typo, not a pipeline issue.
        self.journal.slot(slot_id)

        panel = self.manifest.find_panel(slot_id)
        if panel is None:
            available = self._available_panel_ids()
            raise RuntimeError(
                f"slot {slot_id!r} has no entry in manifest `panels` — "
                f"pipeline likely hasn't emitted the composite for it yet "
                f"(available: {available})"
            )
        return panel

    def _available_panel_ids(self) -> list[str]:
        if self.manifest.panels is None:
            return []
        return [p.id for p in (*self.manifest.panels.main, *self.manifest.panels.supplementary)]

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

    @staticmethod
    def _resolve_extension(panel: Panel, src_abs: Path) -> str:
        """Decide the output extension. Prefer the path suffix; fall back to ``format``."""
        suffix = src_abs.suffix.lstrip(".").lower()
        if suffix:
            return suffix
        if panel.format:
            return panel.format.lower()
        raise RuntimeError(
            f"cannot determine output extension for panel {panel.id!r} — "
            f"path has no suffix and no `format` field"
        )


def _build_caption(title: str | None, caption_seed: str | None) -> str:
    """Join the journal title and pipeline caption_seed into a sidecar string."""
    parts = [s for s in (title, caption_seed) if s]
    if not parts:
        return ""
    return "\n\n".join(parts) + "\n"
