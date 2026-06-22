"""metaomics-scribe — research agent for journal-ready manuscript drafts."""

from metaomics_scribe.figure_builder import BuiltFigure, BuiltTable, FigureBuilder
from metaomics_scribe.journal import Journal, load_journal
from metaomics_scribe.llm import (
    DEFAULT_MODEL,
    ResultsGuardrailError,
    draft_results_section,
)
from metaomics_scribe.manifest import (
    Manifest,
    UnsupportedManifestVersion,
    load_manifest,
)
from metaomics_scribe.manuscript import DraftedManuscript, ManuscriptDrafter
from metaomics_scribe.methodology import Methodology, load_methodology
from metaomics_scribe.stats_extractor import StatsBundle, extract_stats

__version__ = "0.0.0"

__all__ = [
    "DEFAULT_MODEL",
    "BuiltFigure",
    "BuiltTable",
    "DraftedManuscript",
    "FigureBuilder",
    "Journal",
    "Manifest",
    "ManuscriptDrafter",
    "Methodology",
    "ResultsGuardrailError",
    "StatsBundle",
    "UnsupportedManifestVersion",
    "__version__",
    "draft_results_section",
    "extract_stats",
    "load_journal",
    "load_manifest",
    "load_methodology",
]
