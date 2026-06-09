"""Multi-panel figure composer.

Stitches manifest-referenced PNGs into journal-shaped composite figures. The
manifest only points at *already-rendered* PNGs — this module composes them; it
does not re-render plots. That means the source plots' fonts, axes, and colour
scales are fixed at the pipeline stage and can't be tweaked here. Changing them
requires a manifest change (a new figure kind, or a new pre-rendered variant).

Layout rules:
- 1 panel  → 1x1
- 2 panels → 1x2 (side by side)
- 3 panels → 1x3
- 4 panels → 2x2
A panel whose `kind` is absent from the manifest reflows the grid — the slot is
built from however many panels *did* resolve, rather than failing.

Output: `runs/<study_id>/figures/<slot_id>.png` plus a sidecar
`<slot_id>.caption.txt` concatenating each panel's `caption_seed`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from string import ascii_uppercase

from PIL import Image, ImageDraw, ImageFont

from metaomics_scribe.journal import Journal, Panel
from metaomics_scribe.manifest import Figure, Manifest

MM_PER_INCH = 25.4
PANEL_LABEL_PT = 14
PANEL_LABEL_MARGIN_PX = 18


@dataclass(frozen=True)
class ResolvedPanel:
    """A panel whose manifest figure was found, with the absolute path to the PNG."""

    panel: Panel
    figure: Figure
    abs_path: Path


class FigureBuilder:
    """Compose journal-shaped figures from a manifest + journal template."""

    def __init__(self, manifest: Manifest, journal: Journal, output_root: Path | None = None):
        self.manifest = manifest
        self.journal = journal
        self.output_root = Path(output_root) if output_root else Path("runs")

    def _figures_dir(self) -> Path:
        return self.output_root / self.manifest.study.id / "figures"

    def _all_figures(self) -> list[Figure]:
        return [fig for stage in self.manifest.stages.values() for fig in stage.figures]

    def _match_panel(self, panel: Panel) -> ResolvedPanel | None:
        """Find the single manifest figure that satisfies the panel's kind + filters.

        Returns None if no match (the slot will reflow). If multiple candidates
        match the kind and the panel has no disambiguating `metric`/`pair`, the
        first occurrence wins — deterministic and good enough for v0.2.
        """
        for fig in self._all_figures():
            if fig.kind != panel.kind:
                continue
            if panel.metric is not None and fig.metric != panel.metric:
                continue
            if panel.pair is not None and fig.pair != panel.pair:
                continue
            abs_path = self.manifest.resolve_path(fig.path)
            if not abs_path.exists():
                continue
            return ResolvedPanel(panel=panel, figure=fig, abs_path=abs_path)
        return None

    def resolve_panels(self, slot_id: str) -> list[ResolvedPanel]:
        """Return the panels that resolved, in slot order. Missing kinds are dropped."""
        slot = self.journal.slot(slot_id)
        resolved: list[ResolvedPanel] = []
        for p in slot.panels:
            r = self._match_panel(p)
            if r is not None:
                resolved.append(r)
        return resolved

    def build(self, slot_id: str) -> Path:
        """Compose `slot_id` and write `<output_root>/<study_id>/figures/<slot_id>.png`.

        Raises `RuntimeError` if no panels resolve — an empty figure is never a
        useful output. Writes a sidecar `<slot_id>.caption.txt` with concatenated
        `caption_seed` strings.
        """
        slot = self.journal.slot(slot_id)
        resolved = self.resolve_panels(slot_id)
        if not resolved:
            raise RuntimeError(
                f"slot {slot_id!r} has no resolvable panels — all of "
                f"{[p.kind for p in slot.panels]} were missing from the manifest"
            )

        target_width_px, target_height_px = self._slot_pixel_box()
        composite = self._compose(resolved, target_width_px, target_height_px)

        out_dir = self._figures_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_png = out_dir / f"{slot_id}.png"
        composite.save(out_png, format="PNG", dpi=(self.journal.figures.dpi_min,) * 2)

        caption_path = out_dir / f"{slot_id}.caption.txt"
        caption_path.write_text(self._build_caption(slot.title, resolved), encoding="utf-8")
        return out_png

    def _slot_pixel_box(self) -> tuple[int, int]:
        """Resolve the journal's double-column width x max height in pixels at dpi_min.

        Defaults to double-column when available (most multi-panel figures want
        it); falls back to single-column otherwise. Max height defaults to a
        4:3 box of the width if the journal doesn't constrain it.
        """
        cw = self.journal.figures.column_widths_mm
        width_mm = cw.double or cw.one_half or cw.single
        max_h_mm = self.journal.figures.max_height_mm or (width_mm * 0.75)
        dpi = self.journal.figures.dpi_min
        return (int(width_mm / MM_PER_INCH * dpi), int(max_h_mm / MM_PER_INCH * dpi))

    @staticmethod
    def _layout_for(n_panels: int) -> tuple[int, int]:
        """Rows, cols. 1→1x1, 2→1x2, 3→1x3, 4→2x2. >4 not supported in v0.2."""
        if n_panels == 1:
            return (1, 1)
        if n_panels == 2:
            return (1, 2)
        if n_panels == 3:
            return (1, 3)
        if n_panels == 4:
            return (2, 2)
        raise ValueError(f"v0.2 supports 1-4 panels per slot, got {n_panels}")

    def _compose(
        self, resolved: list[ResolvedPanel], target_w: int, target_h: int
    ) -> Image.Image:
        rows, cols = self._layout_for(len(resolved))
        cell_w = target_w // cols
        cell_h = target_h // rows
        canvas = Image.new("RGB", (target_w, target_h), "white")
        font = _load_label_font(self.journal.figures.dpi_min)

        for idx, rp in enumerate(resolved):
            row, col = divmod(idx, cols)
            with Image.open(rp.abs_path) as src:
                panel_img = _fit_into(src.convert("RGB"), cell_w, cell_h)
            x = col * cell_w + (cell_w - panel_img.width) // 2
            y = row * cell_h + (cell_h - panel_img.height) // 2
            canvas.paste(panel_img, (x, y))
            _draw_panel_label(canvas, ascii_uppercase[idx], x, y, font)
        return canvas

    @staticmethod
    def _build_caption(slot_title: str | None, resolved: list[ResolvedPanel]) -> str:
        header = slot_title or ""
        lines: list[str] = []
        if header:
            lines.append(header)
            lines.append("")
        for idx, rp in enumerate(resolved):
            seed = rp.figure.caption_seed or f"(no caption seed for {rp.figure.kind})"
            lines.append(f"({ascii_uppercase[idx]}) {seed}")
        return "\n".join(lines) + "\n"


def _fit_into(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    """Resize preserving aspect ratio so the image fits inside max_w x max_h."""
    scale = min(max_w / img.width, max_h / img.height)
    new_w = max(1, int(img.width * scale))
    new_h = max(1, int(img.height * scale))
    return img.resize((new_w, new_h), Image.Resampling.LANCZOS)


def _load_label_font(dpi: int) -> ImageFont.ImageFont:
    """Best-effort load of a bold sans-serif for panel labels. Falls back to default."""
    size_px = max(12, int(PANEL_LABEL_PT * dpi / 72))
    for candidate in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(candidate, size=size_px)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_panel_label(
    canvas: Image.Image, label: str, x: int, y: int, font: ImageFont.ImageFont
) -> None:
    draw = ImageDraw.Draw(canvas)
    draw.text(
        (x + PANEL_LABEL_MARGIN_PX, y + PANEL_LABEL_MARGIN_PX),
        label,
        fill="black",
        font=font,
    )


__all__ = ["FigureBuilder", "ResolvedPanel"]
