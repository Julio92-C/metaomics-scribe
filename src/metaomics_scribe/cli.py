"""Command-line entrypoint for metaomics-scribe.

Single ``draft`` subcommand that mirrors what :class:`ManuscriptDrafter`
exposes: read a manifest + journal, optionally apply a methodology template,
optionally call Claude to fill Results subsections, and optionally render a
DOCX via pandoc.

Installed as the ``metaomics-scribe`` console script via the ``[project.scripts]``
entry in ``pyproject.toml``. Invoke through uv:

    uv run metaomics-scribe draft path/to/manifest.json --llm

The deterministic path requires no extra dependencies. ``--llm`` requires the
optional ``[llm]`` extra (``uv sync --extra llm``) and a logged-in ``claude``
CLI (the Claude Agent SDK uses the same subscription auth — no API key).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .journal import load_journal
from .llm import DEFAULT_MODEL, ResultsGuardrailError
from .manifest import UnsupportedManifestVersion, load_manifest
from .manuscript import ManuscriptDrafter
from .methodology import load_methodology

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_JOURNAL = REPO_ROOT / "journals" / "frontiers_microbiome.yaml"
DEFAULT_METHODOLOGY = REPO_ROOT / "methods" / "metagenomics_pipeline_automation.yaml"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="metaomics-scribe",
        description="Draft a journal-ready manuscript from a metagenomics manifest.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    draft = subparsers.add_parser(
        "draft",
        help="Render manuscript.md (and optionally manuscript.docx) for a manifest.",
    )
    draft.add_argument(
        "manifest",
        type=Path,
        help="Path to manifest.json (or the directory containing one).",
    )
    draft.add_argument(
        "--journal",
        type=Path,
        default=DEFAULT_JOURNAL,
        help="Journal template YAML (default: Frontiers in Microbiology).",
    )
    draft.add_argument(
        "--methods",
        type=Path,
        default=DEFAULT_METHODOLOGY,
        help=(
            "Methodology template YAML for the Bioinformatic Pipeline subsection. "
            "Pass --no-methods to skip."
        ),
    )
    draft.add_argument(
        "--no-methods",
        dest="methods",
        action="store_const",
        const=None,
        help=(
            "Render the generic one-sentence pipeline paragraph instead of "
            "the per-stage methodology."
        ),
    )
    draft.add_argument(
        "--output-root",
        type=Path,
        default=Path("runs"),
        help="Parent directory for the per-study output folder (default: ./runs).",
    )
    draft.add_argument(
        "--llm",
        action="store_true",
        help=(
            "Fill Results subsections via Claude. Uses the Claude Agent SDK, "
            "which authenticates through your local `claude` CLI (Pro/Max "
            "subscription works; no ANTHROPIC_API_KEY needed). Requires the "
            "optional `llm` extra: uv sync --extra llm."
        ),
    )
    draft.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Claude model id for --llm drafting (default: {DEFAULT_MODEL}).",
    )
    draft.add_argument(
        "--docx",
        action="store_true",
        help="Also render manuscript.docx via pandoc.",
    )
    draft.add_argument(
        "--reference-doc",
        type=Path,
        default=None,
        help="Frontiers-styled .docx whose styles pandoc should apply (--docx only).",
    )
    return parser


def _draft(args: argparse.Namespace) -> int:
    try:
        manifest = load_manifest(args.manifest)
    except UnsupportedManifestVersion as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    journal = load_journal(args.journal)
    methodology = load_methodology(args.methods) if args.methods is not None else None

    drafter = ManuscriptDrafter(
        manifest,
        journal,
        output_root=args.output_root,
        methodology=methodology,
    )

    if args.llm:
        try:
            drafted = drafter.draft_with_llm(model=args.model)
        except RuntimeError as exc:
            # default_caller() raises RuntimeError when claude-agent-sdk
            # isn't installed — surface it as a non-crashing CLI error.
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except ResultsGuardrailError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3
    else:
        drafted = drafter.draft()

    print(f"wrote {drafted.markdown_path}")

    if args.docx:
        drafted = drafter.draft_docx(reference_doc=args.reference_doc)
        print(f"wrote {drafted.docx_path}")

    return 0


def main(argv: list[str] | None = None) -> int:
    """Entrypoint for the ``metaomics-scribe`` console script."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "draft":
        return _draft(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
