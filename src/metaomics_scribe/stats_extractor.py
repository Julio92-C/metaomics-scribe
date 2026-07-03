"""Deterministic parsers for the manifest's ``stats_text`` files.

The pipeline emits human-readable statistical results as plain-text files
(``alpha_stats.txt``, ``permanova.txt``, ``permdisp.txt``). v0.4.1 needs those
values in a structured form so the LLM that drafts each results subsection can
*select* claims from a fixed list rather than inventing numbers. This module
walks the manifest, reads each ``stats_text`` entry by ``kind``, and returns a
typed :class:`StatsBundle` keyed by stage id.

All parsing is regex-based and side-effect-free. Files that don't exist or
don't match the expected format are skipped (with the unparsed text retained
under ``StageStats.raw_text``) so a single malformed file never blocks a draft.
"""

from __future__ import annotations

import csv
import re
from io import StringIO
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from .manifest import Manifest, StatsText, Table


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class KruskalRow(_Model):
    """One per-metric Kruskal-Wallis test row from ``alpha_stats.txt``."""

    metric: str
    grouping_variable: str
    p_raw: float
    p_adj: float


class PermanovaResult(_Model):
    """Parsed adonis2 PERMANOVA table (one Model row).

    ``source`` is the stats_text file basename without extension — used by the
    LLM to disambiguate when a stage declares multiple PERMANOVAs (e.g.
    ``permanova`` for Bray-Curtis and ``jaccard_permanova`` for Jaccard).
    """

    source: str
    n_permutations: int | None = None
    df_model: int | None = None
    df_residual: int | None = None
    df_total: int | None = None
    sum_of_sqs_model: float | None = None
    sum_of_sqs_residual: float | None = None
    R2: float | None = None
    F: float | None = None
    p_value: float | None = None


class PermdispResult(_Model):
    """Parsed PERMDISP homogeneity-of-dispersion result.

    ``betadisper`` output isn't a fixed shape in the wild — the pipeline's
    text dumps usually carry an F statistic and a p-value plus the per-group
    mean distances. We capture only the fields we can extract robustly.
    ``source`` mirrors :class:`PermanovaResult.source`.
    """

    source: str
    n_permutations: int | None = None
    F: float | None = None
    p_value: float | None = None
    group_mean_distances: dict[str, float] = {}


class MantelPair(_Model):
    """One pairwise Mantel-test row from ``mantel_pairwise_padj.csv``.

    The network stage exports cross-domain coupling as a CSV rather than a
    ``.txt`` file — the columns are ``domain_a``, ``domain_b``, ``mantel_r``,
    ``p_raw``, ``p_adj``, ``method``. ``source`` mirrors the convention used
    for PERMANOVA/PERMDISP: the file stem so the LLM can disambiguate if a
    stage ever declares multiple Mantel tables (e.g. per distance metric).
    """

    source: str
    domain_a: str
    domain_b: str
    mantel_r: float
    p_raw: float
    p_adj: float
    method: str = "BH"


class StageStats(_Model):
    """Every parsed stats artifact for a single manifest stage.

    PERMANOVA and PERMDISP are lists because a stage may declare multiple
    files of the same ``kind`` (typically one per distance metric — Bray-Curtis
    plus Jaccard). Each parsed entry carries its source filename so the LLM
    can label quoted values correctly.
    """

    stage_id: str
    kruskal: list[KruskalRow] = []
    permanovas: list[PermanovaResult] = []
    permdisps: list[PermdispResult] = []
    mantels: list[MantelPair] = []
    # Unparsed fallback: kind → raw text. Used for grounding the LLM when we
    # recognise a stats file but don't have a structured parser for it yet.
    raw_text: dict[str, str] = {}


class StatsBundle(_Model):
    """All structured stats extracted from a manifest, keyed by stage id."""

    by_stage: dict[str, StageStats] = {}


# ---------------------------------------------------------------------------
# Regex patterns (compiled once; documented inline)
# ---------------------------------------------------------------------------

# Kruskal-Wallis line: "Metric Name ~ Variable: p_raw = 0.123, p_adj (BH) = 0.456"
_KRUSKAL_LINE = re.compile(
    r"^(?P<metric>.+?)\s*~\s*(?P<var>[\w.]+)\s*:\s*"
    r"p_raw\s*=\s*(?P<p_raw>[0-9eE.+\-]+)\s*,\s*"
    r"p_adj\s*\(BH\)\s*=\s*(?P<p_adj>[0-9eE.+\-]+)\s*$"
)

# adonis2 / PERMANOVA table — vegan prints it as:
#   Model     2   0.6261 0.14461 1.2679 0.2193
#   Residual 15   3.7036 0.85539
#   Total    17   4.3297 1.00000
_PERMANOVA_MODEL = re.compile(
    r"^Model\s+(?P<df>\d+)\s+(?P<ss>[0-9.eE+\-]+)\s+(?P<r2>[0-9.eE+\-]+)\s+"
    r"(?P<f>[0-9.eE+\-]+)\s+(?P<p>[0-9.eE+\-]+)\s*$"
)
_PERMANOVA_RESIDUAL = re.compile(
    r"^Residual\s+(?P<df>\d+)\s+(?P<ss>[0-9.eE+\-]+)\s+[0-9.eE+\-]+\s*$"
)
_PERMANOVA_TOTAL = re.compile(r"^Total\s+(?P<df>\d+)\s+[0-9.eE+\-]+\s+[0-9.eE+\-]+\s*$")
_PERMANOVA_PERMS = re.compile(r"^Number of permutations\s*:\s*(?P<n>\d+)\s*$")

# PERMDISP — `permutest(betadisper(...))` output. Lines we care about:
#   Permutation: free / Number of permutations: 9999
#   Groups       3  0.005  0.0018  4.21    999  0.012
_PERMDISP_GROUPS = re.compile(
    r"^Groups\s+\d+\s+[0-9.eE+\-]+\s+[0-9.eE+\-]+\s+"
    r"(?P<f>[0-9.eE+\-]+)\s+\d+\s+(?P<p>[0-9.eE+\-]+)\s*$"
)
# Per-group mean distances appear as: "Group_Name  0.4321"
_PERMDISP_MEAN_LINE = re.compile(r"^(?P<group>\S[\S ]*?)\s{2,}(?P<dist>[0-9.eE+\-]+)\s*$")


# ---------------------------------------------------------------------------
# Per-kind parsers
# ---------------------------------------------------------------------------


def _parse_kruskal(text: str) -> list[KruskalRow]:
    rows: list[KruskalRow] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = _KRUSKAL_LINE.match(stripped)
        if m is None:
            continue
        try:
            rows.append(
                KruskalRow(
                    metric=m.group("metric").strip(),
                    grouping_variable=m.group("var").strip(),
                    p_raw=float(m.group("p_raw")),
                    p_adj=float(m.group("p_adj")),
                )
            )
        except (ValueError, TypeError):
            continue
    return rows


def _parse_permanova(text: str, source: str) -> PermanovaResult | None:
    result = PermanovaResult(source=source)
    matched = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if m := _PERMANOVA_PERMS.match(stripped):
            result.n_permutations = int(m.group("n"))
            matched = True
        elif m := _PERMANOVA_MODEL.match(stripped):
            result.df_model = int(m.group("df"))
            result.sum_of_sqs_model = float(m.group("ss"))
            result.R2 = float(m.group("r2"))
            result.F = float(m.group("f"))
            result.p_value = float(m.group("p"))
            matched = True
        elif m := _PERMANOVA_RESIDUAL.match(stripped):
            result.df_residual = int(m.group("df"))
            result.sum_of_sqs_residual = float(m.group("ss"))
            matched = True
        elif m := _PERMANOVA_TOTAL.match(stripped):
            result.df_total = int(m.group("df"))
            matched = True
    return result if matched else None


def _parse_mantel_pairwise(text: str, source: str) -> list[MantelPair]:
    """Parse a Mantel pairwise-comparison CSV emitted by the ``network`` stage.

    Expected columns (case-sensitive): ``domain_a``, ``domain_b``, ``mantel_r``,
    ``p_raw``, ``p_adj``, ``method``. Rows with missing / unparseable numeric
    fields are skipped rather than raising, matching the fault tolerance of
    the ``.txt`` parsers above — one malformed row never blocks a draft.
    """
    rows: list[MantelPair] = []
    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames is None:
        return rows
    required = {"domain_a", "domain_b", "mantel_r", "p_raw", "p_adj"}
    if not required.issubset(reader.fieldnames):
        return rows
    for row in reader:
        try:
            rows.append(
                MantelPair(
                    source=source,
                    domain_a=row["domain_a"].strip(),
                    domain_b=row["domain_b"].strip(),
                    mantel_r=float(row["mantel_r"]),
                    p_raw=float(row["p_raw"]),
                    p_adj=float(row["p_adj"]),
                    method=(row.get("method") or "BH").strip() or "BH",
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return rows


def _parse_permdisp(text: str, source: str) -> PermdispResult | None:
    result = PermdispResult(source=source)
    in_mean_distances = False
    matched = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            in_mean_distances = False
            continue
        if m := _PERMANOVA_PERMS.match(stripped):
            result.n_permutations = int(m.group("n"))
            matched = True
            continue
        if m := _PERMDISP_GROUPS.match(stripped):
            result.F = float(m.group("f"))
            result.p_value = float(m.group("p"))
            matched = True
            continue
        if stripped.startswith("Average distance to median"):
            in_mean_distances = True
            continue
        if in_mean_distances:
            if m := _PERMDISP_MEAN_LINE.match(raw):
                try:
                    result.group_mean_distances[m.group("group").strip()] = float(
                        m.group("dist")
                    )
                    matched = True
                except (ValueError, TypeError):
                    pass
    return result if matched else None


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def _read_stats_file(manifest: Manifest, entry: StatsText | Table) -> str | None:
    """Read the on-disk text of a stats artifact (``.txt`` or ``.csv``).

    The single accessor is shared across the ``.txt`` parsers (Kruskal-Wallis,
    PERMANOVA, PERMDISP) and the CSV parsers (Mantel) — both entry types
    expose a ``.path`` attribute, so the function is agnostic to file format.
    """
    try:
        path = manifest.resolve_path(entry.path)
    except RuntimeError:
        # Manifest was constructed in-memory and has no on-disk anchor; nothing
        # to read but not an error.
        return None
    if not Path(path).is_file():
        return None
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def _stats_source_id(entry: StatsText | Table) -> str:
    """Stable, human-readable id for a stats source (``.txt`` or ``.csv``).

    Used by the LLM grounding pack to disambiguate multiple files of the same
    ``kind`` (e.g. ``permanova`` for Bray-Curtis vs ``jaccard_permanova`` for
    Jaccard). We use the file stem because ``permanova`` and
    ``jaccard_permanova`` already carry the distance metric in the basename.
    """
    return Path(entry.path).stem


def extract_stats(manifest: Manifest) -> StatsBundle:
    """Walk every ``stats_text`` entry in the manifest and parse what we can.

    Returns a :class:`StatsBundle` where each completed stage with parseable
    stats files has a populated :class:`StageStats`. Stages whose files are
    missing or whose ``kind`` we don't recognise are simply absent from the
    bundle — they don't raise. The LLM call falls back to ``raw_text`` for
    unknown kinds.

    A stage may declare multiple files of the same ``kind`` (typically per
    distance metric). All parsed entries are kept in the per-kind list with
    their source filename so the LLM can attribute quoted values correctly.
    """
    bundle = StatsBundle()
    for stage_id, stage in manifest.stages.items():
        if not stage.stats_text and not stage.tables:
            continue
        stage_stats = StageStats(stage_id=stage_id)
        any_parsed = False
        for entry in stage.stats_text:
            text = _read_stats_file(manifest, entry)
            if text is None:
                continue
            source = _stats_source_id(entry)
            if entry.kind == "kruskal_wallis":
                rows = _parse_kruskal(text)
                if rows:
                    # Multiple kruskal files in one stage are uncommon, but
                    # accumulate just in case.
                    stage_stats.kruskal.extend(rows)
                    any_parsed = True
            elif entry.kind == "permanova":
                parsed = _parse_permanova(text, source=source)
                if parsed is not None:
                    stage_stats.permanovas.append(parsed)
                    any_parsed = True
            elif entry.kind == "permdisp":
                parsed = _parse_permdisp(text, source=source)
                if parsed is not None:
                    stage_stats.permdisps.append(parsed)
                    any_parsed = True
            else:
                # Unknown kind — keep the raw text around so the LLM can still
                # reference it via the grounding pack. Key by source id so two
                # files of the same unknown kind don't clobber each other.
                stage_stats.raw_text[source] = text
                any_parsed = True
        # CSV-shaped stats live in `tables` rather than `stats_text`. Only
        # recognised kinds are ingested here; other tables (composition
        # long-form, sankey/chord source data, etc.) remain outside the
        # structured stats bundle and are consumed by the figure builder /
        # LLM grounding pack via the manifest's `stage_summaries` instead.
        for entry in stage.tables:
            if entry.kind != "mantel_pairwise_padj":
                continue
            text = _read_stats_file(manifest, entry)
            if text is None:
                continue
            source = _stats_source_id(entry)
            pairs = _parse_mantel_pairwise(text, source=source)
            if pairs:
                stage_stats.mantels.extend(pairs)
                any_parsed = True
        if any_parsed:
            bundle.by_stage[stage_id] = stage_stats
    return bundle
