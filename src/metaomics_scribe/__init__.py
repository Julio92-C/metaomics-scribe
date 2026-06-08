"""metaomics-scribe — research agent for journal-ready manuscript drafts."""

from metaomics_scribe.manifest import (
    Manifest,
    UnsupportedManifestVersion,
    load_manifest,
)

__version__ = "0.0.0"

__all__ = [
    "Manifest",
    "UnsupportedManifestVersion",
    "__version__",
    "load_manifest",
]
