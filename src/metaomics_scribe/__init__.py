"""metaomics-scribe — research agent for journal-ready manuscript drafts."""

from metaomics_scribe.figure_builder import FigureBuilder
from metaomics_scribe.journal import Journal, load_journal
from metaomics_scribe.manifest import (
    Manifest,
    UnsupportedManifestVersion,
    load_manifest,
)

__version__ = "0.0.0"

__all__ = [
    "FigureBuilder",
    "Journal",
    "Manifest",
    "UnsupportedManifestVersion",
    "__version__",
    "load_journal",
    "load_manifest",
]
