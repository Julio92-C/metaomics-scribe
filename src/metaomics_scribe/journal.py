"""Journal template loader.

Each supported journal lives in `journals/<id>.yaml` as pure config: figure
dimensions, citation style, word caps, and the IMRaD section order of a
manuscript drafted in that journal's style. Adding or tweaking a journal is a
config edit, not a code change — these Pydantic models exist only to validate
the YAML and expose typed access to it.

The journal is *study-agnostic*. It does not enumerate figure slot ids or pin
slots to subsections — that information lives in the pipeline's manifest (see
the `panels` stage convention in `docs/MANIFEST_SCHEMA.md`). The same journal
template drives any conforming pipeline run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class JournalMeta(_Model):
    id: str
    name: str
    publisher: str | None = None
    url: str | None = None
    article_type: str | None = None


class Subsection(_Model):
    """A subsection inside a manuscript section.

    Subsection ids are abstract topic names (``resistome``, ``virulome``, …).
    They do not list which figures live where — that mapping is provided by
    the pipeline via each panel composite's ``Figure.subsection`` field.
    """

    id: str
    title: str | None = None


class Section(_Model):
    id: str
    max_words: int | None = None
    structured: bool | None = None
    min: int | None = None
    max: int | None = None
    grounded_in: list[str] | None = None
    subsections: list[Subsection] | None = None


class Manuscript(_Model):
    citation_style: str
    max_word_count: int | None = None
    max_main_figures: int | None = None
    max_main_tables: int | None = None
    sections: list[Section]


class ColumnWidths(_Model):
    single: float
    one_half: float | None = None
    double: float | None = None


class Caption(_Model):
    position: Literal["above", "below"] | None = None
    bold_label: bool | None = None
    max_words: int | None = None


class FiguresSpec(_Model):
    column_widths_mm: ColumnWidths
    max_height_mm: float | None = None
    font_min_pt: float | None = None
    font_max_pt: float | None = None
    dpi_min: int = 300
    dpi_preferred: int | None = None
    formats: list[str] | None = None
    preferred_format: str | None = None
    caption: Caption | None = None


class TablesSpec(_Model):
    format: str | None = None
    caption: Caption | None = None


class SupplementarySpec(_Model):
    numbering: str | None = None
    separate_file: bool | None = None
    max_items: int | None = None
    formats_accepted: list[str] | None = None


class Journal(_Model):
    journal: JournalMeta
    manuscript: Manuscript
    figures: FiguresSpec
    tables: TablesSpec | None = None
    supplementary: SupplementarySpec | None = None


def load_journal(path: str | Path) -> Journal:
    """Load and validate a journal YAML template."""
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Journal.model_validate(data)
