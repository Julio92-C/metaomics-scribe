"""metaomics-scribe — research agent for journal-ready manuscript drafts."""

from metaomics_scribe.figure_builder import BuiltFigure, BuiltTable, FigureBuilder
from metaomics_scribe.journal import Journal, load_journal
from metaomics_scribe.manifest import (
    Manifest,
    UnsupportedManifestVersion,
    load_manifest,
)
from metaomics_scribe.manuscript import DraftedManuscript, ManuscriptDrafter
from metaomics_scribe.methodology import Methodology, load_methodology

__version__ = "0.0.0"

__all__ = [
    "BuiltFigure",
    "BuiltTable",
    "DraftedManuscript",
    "FigureBuilder",
    "Journal",
    "Manifest",
    "ManuscriptDrafter",
    "Methodology",
    "UnsupportedManifestVersion",
    "__version__",
    "load_journal",
    "load_manifest",
    "load_methodology",
]
