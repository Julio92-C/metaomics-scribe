"""Manuscript drafter (v0.4 — methods-only first cut).

The drafter consumes the validated manifest + journal template and emits a
Frontiers-style manuscript draft as Markdown under
``runs/<study_id>/manuscript.md``. v0.4.0 covers only the *Materials and
Methods* section because it is deterministic — every claim is templated
directly from a manifest field, so the "no invented numbers" invariant in
``CLAUDE.md`` is enforced by construction.

Other IMRaD sections appear as stubs in the Markdown so a human author (or a
later milestone) can fill them in. The drafter does not call an LLM in
v0.4.0.

DOCX export is optional. ``draft_docx()`` shells out to ``pandoc`` if it is
on PATH; if not, it raises a clear error rather than silently skipping.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .journal import Journal, Section
from .llm import (
    DEFAULT_MODEL,
    LLMCaller,
    default_caller,
    draft_figure_caption,
    draft_results_section,
)
from .manifest import Figure, Manifest
from .methodology import Methodology
from .stats_extractor import extract_stats


@dataclass(frozen=True)
class DraftedManuscript:
    """Paths to the artifacts produced by a draft run."""

    markdown_path: Path
    docx_path: Path | None = None


class ManuscriptDrafter:
    """Render a manuscript draft from a manifest + journal template."""

    def __init__(
        self,
        manifest: Manifest,
        journal: Journal,
        output_root: str | Path = "runs",
        methodology: Methodology | None = None,
    ):
        self.manifest = manifest
        self.journal = journal
        self.output_root = Path(output_root)
        self.methodology = methodology
        # Populated by draft_with_llm(): subsection_id -> Results prose,
        # composite_slot_id -> figure caption. Persisted across subsequent
        # draft() calls so the docx re-render keeps every LLM-generated
        # output. Not part of the public API.
        self._llm_prose: dict[str, str] | None = None
        self._llm_captions: dict[str, str] | None = None

    def _manuscript_dir(self) -> Path:
        return self.output_root / self.manifest.study.id

    def draft(self) -> DraftedManuscript:
        """Render every IMRaD section the journal declares and write
        ``manuscript.md`` under ``runs/<study_id>/``.

        Sections not yet implemented are written as stubs flagged with a
        ``> TODO`` marker so the human author can see what is missing.
        """
        out_dir = self._manuscript_dir()
        out_dir.mkdir(parents=True, exist_ok=True)

        chunks: list[str] = [f"# {self.manifest.study.name}\n"]
        for section in self.journal.manuscript.sections:
            chunks.append(self._render_section(section))

        markdown_path = out_dir / "manuscript.md"
        markdown_path.write_text("\n".join(chunks) + "\n", encoding="utf-8")
        return DraftedManuscript(markdown_path=markdown_path)

    def draft_with_llm(
        self,
        caller: LLMCaller | None = None,
        *,
        model: str = DEFAULT_MODEL,
    ) -> DraftedManuscript:
        """Draft the manuscript with LLM-generated Results subsections.

        Calls Claude once per results subsection declared in the journal.
        The deterministic Methods section is unchanged from :meth:`draft`.
        Each LLM-generated paragraph is validated against the grounding pack
        (parsed stats + manifest fields); a numeric token in the prose that
        isn't in the grounding raises
        :class:`metaomics_scribe.llm.ResultsGuardrailError` and aborts the
        draft so a fabricated number never reaches disk.

        ``caller`` is any ``(system, user, model) -> str`` callable.
        Production code can omit it — the default uses the Claude Agent SDK,
        which authenticates through the local ``claude`` CLI (subscription
        auth, no API key required). Tests pass a lambda.
        """
        if caller is None:
            caller = default_caller()

        stats_bundle = extract_stats(self.manifest)

        results_section = self._find_results_section()
        prose: dict[str, str] = {}
        prose_failures: list[tuple[str, str]] = []
        if results_section is not None and results_section.subsections:
            grouped = self._composites_by_subsection()
            subs_with_composites = [
                sub for sub in results_section.subsections
                if grouped.get(sub.id)
            ]
            for i, sub in enumerate(subs_with_composites, start=1):
                composites = grouped[sub.id]
                print(
                    f"[metaomics-scribe] prose {i}/{len(subs_with_composites)}: {sub.id}",
                    file=sys.stderr,
                    flush=True,
                )
                try:
                    prose[sub.id] = draft_results_section(
                        caller,
                        manifest=self.manifest,
                        stats_bundle=stats_bundle,
                        subsection=sub,
                        composites=composites,
                        model=model,
                    )
                except Exception as exc:  # noqa: BLE001 — SDK failure surface is broad
                    prose_failures.append((sub.id, str(exc)))
                    print(
                        f"[metaomics-scribe] prose {sub.id} failed ({exc}); "
                        f"falling back to TODO stub",
                        file=sys.stderr,
                        flush=True,
                    )
        if prose_failures:
            print(
                f"[metaomics-scribe] {len(prose_failures)} Results subsection(s) "
                f"fell back to TODO stub: "
                f"{', '.join(sub for sub, _ in prose_failures)}",
                file=sys.stderr,
                flush=True,
            )

        # Caption every panel composite. Per-call failures are tolerated so
        # one flaky SDK response doesn't discard the whole batch — the slot
        # falls back to the manifest's caption_seed (surfaced via
        # _render_figure_block) and a warning is printed. Prose failures are
        # tolerated the same way above via the deterministic stub.
        captions: dict[str, str] = {}
        caption_failures: list[tuple[str, str]] = []
        composites = [c for c in self._panel_composites() if c.slot]
        for i, composite in enumerate(composites, start=1):
            slot = composite.slot  # non-None by filter above
            print(
                f"[metaomics-scribe] caption {i}/{len(composites)}: {slot}",
                file=sys.stderr,
                flush=True,
            )
            try:
                captions[slot] = draft_figure_caption(
                    caller,
                    manifest=self.manifest,
                    stats_bundle=stats_bundle,
                    composite=composite,
                    model=model,
                )
            except Exception as exc:  # noqa: BLE001 — SDK failure surface is broad
                caption_failures.append((slot, str(exc)))
                print(
                    f"[metaomics-scribe] caption {slot} failed ({exc}); "
                    f"falling back to caption_seed",
                    file=sys.stderr,
                    flush=True,
                )
        if caption_failures:
            print(
                f"[metaomics-scribe] {len(caption_failures)}/{len(composites)} "
                f"captions fell back to caption_seed: "
                f"{', '.join(slot for slot, _ in caption_failures)}",
                file=sys.stderr,
                flush=True,
            )

        # Set both persistently so subsequent self.draft() calls (notably
        # draft_docx(), which re-renders for pandoc) preserve every LLM
        # output. Clearing here would silently revert any downstream render
        # to the deterministic stub.
        self._llm_prose = prose
        self._llm_captions = captions
        return self.draft()

    def draft_docx(self, reference_doc: Path | None = None) -> DraftedManuscript:
        """Render the draft and convert it to DOCX via pandoc.

        ``reference_doc`` is an optional Frontiers-styled .docx whose styles
        pandoc will apply. Raises ``RuntimeError`` if pandoc isn't on PATH or
        the conversion fails — silently skipping a DOCX export would hide a
        submission-blocking problem.
        """
        drafted = self.draft()
        if shutil.which("pandoc") is None:
            raise RuntimeError(
                "pandoc is not on PATH — install it or call draft() instead of draft_docx()"
            )

        docx_path = drafted.markdown_path.with_suffix(".docx")
        # Run pandoc with cwd = markdown_path.parent so relative image links
        # in the markdown (e.g. `figures/fig01.tiff`, written by FigureBuilder
        # into runs/<study>/figures/) resolve regardless of the caller's CWD.
        # Without this, invoking `metaomics-scribe` from the repo root makes
        # pandoc look for `figures/…` under the repo root, silently drop every
        # image, and produce a text-only DOCX.
        cmd = ["pandoc", drafted.markdown_path.name, "-o", docx_path.name]
        if reference_doc is not None:
            cmd += [f"--reference-doc={reference_doc}"]
        result = subprocess.run(
            cmd,
            cwd=drafted.markdown_path.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"pandoc failed (exit {result.returncode}):\n"
                f"  stdout: {result.stdout.strip()}\n  stderr: {result.stderr.strip()}"
            )
        return DraftedManuscript(markdown_path=drafted.markdown_path, docx_path=docx_path)

    # ------------------------------------------------------------------
    # Section rendering
    # ------------------------------------------------------------------

    def _render_section(self, section: Section) -> str:
        """Dispatch to the per-section renderer; falls back to a TODO stub."""
        renderer = {
            "title": self._render_title,
            "abstract": self._render_stub,
            "keywords": self._render_stub,
            "introduction": self._render_stub,
            "methods": self._render_methods,
            "results": self._render_results_stub,
            "discussion": self._render_stub,
            "conclusion": self._render_stub,
        }.get(section.id, self._render_stub)
        return renderer(section)

    def _render_title(self, section: Section) -> str:
        # Title is rendered as the document H1 already; this slot emits a
        # placeholder for the running head / submission title page.
        return f"<!-- section: {section.id} — emitted as document H1 above -->"

    def _render_stub(self, section: Section) -> str:
        heading = self._human_heading(section.id)
        lines = [f"## {heading}", "", f"> TODO: {section.id} section not yet drafted by v0.4.0."]
        if section.subsections:
            for sub in section.subsections:
                sub_heading = sub.title or self._human_heading(sub.id)
                lines += ["", f"### {sub_heading}", "", f"> TODO: {sub.id}."]
        return "\n".join(lines) + "\n"

    def _render_results_stub(self, section: Section) -> str:
        """Results stub that lists each subsection's composites, drops in the
        LLM (or stub) prose, then embeds every composite as an inline image +
        caption so the rendered DOCX is previewable end-to-end."""
        heading = self._human_heading(section.id)
        lines = [f"## {heading}", ""]
        if not section.subsections:
            lines.append("> TODO: results subsections not declared in the journal.")
            return "\n".join(lines) + "\n"

        # Group every panel composite by subsection (or None when absent).
        groups: dict[str | None, list[Figure]] = {}
        for fig in self._panel_composites():
            groups.setdefault(fig.subsection, []).append(fig)

        for sub in section.subsections:
            sub_heading = sub.title or self._human_heading(sub.id)
            lines += [f"### {sub_heading}", ""]
            figs = groups.get(sub.id, [])
            if figs:
                slot_labels = ", ".join(
                    f"`{f.slot or '(no slot)'}`" for f in figs
                )
                lines.append(f"Figures in this subsection: {slot_labels}.")
            else:
                lines.append(
                    f"> TODO: no `panel_composite` carries `subsection: {sub.id}` in the manifest."
                )
            lines.append("")
            llm_prose = (self._llm_prose or {}).get(sub.id)
            if llm_prose:
                lines.append(llm_prose.strip())
            else:
                lines.append(f"> TODO: prose for {sub.id} not yet drafted by v0.4.0.")
            lines.append("")
            for fig in figs:
                lines.extend(self._render_figure_block(fig))

        unassigned = groups.get(None, [])
        if unassigned:
            lines += [
                "### Figures awaiting subsection assignment",
                "",
                "> The pipeline did not emit a `subsection` field on the following "
                "composites — the human author must place them into a results "
                "subsection above:",
                "",
            ]
            for fig in unassigned:
                lines.extend(self._render_figure_block(fig))
        return "\n".join(lines) + "\n"

    def _render_figure_block(self, fig: Figure) -> list[str]:
        """Inline image + caption for one panel composite.

        Caption resolution: LLM-generated caption if available, else the
        manifest's caption_seed (often a placeholder for composites — the
        human author can rewrite), else "(no caption)".
        """
        slot = fig.slot or "(no slot)"
        rel_path = f"figures/{slot}.{_composite_extension(fig)}"
        caption = (
            (self._llm_captions or {}).get(slot)
            or (fig.caption_seed or "").strip()
            or "(no caption)"
        )
        return [
            f"![]({rel_path})",
            "",
            f"**Figure `{slot}`.** {caption}",
            "",
        ]

    def _render_methods(self, section: Section) -> str:
        """Deterministic methods section. Every number / parameter is drawn
        from a manifest field — nothing here is invented."""
        s = self.manifest.study
        f = self.manifest.config.filters
        st = self.manifest.config.stats
        p = self.manifest.pipeline

        # Study design — every group, every count, sourced from the manifest.
        groups = _join(s.group_levels)
        re_clause = (
            f" The variable `{s.random_effect}` was treated as a random effect."
            if s.random_effect
            else ""
        )
        fe_clause = (
            f" Fixed effects: {_join([f'`{x}`' for x in s.fixed_effects])}."
            if s.fixed_effects
            else ""
        )
        ctrl_clause = (
            f" Negative controls (n = {s.n_controls}: {_join(s.control_ids or [])}) "
            "were processed alongside biological samples and excluded from downstream "
            "statistical analysis."
            if s.n_controls > 0
            else ""
        )
        desc_clause = f" {s.description}" if s.description else ""

        study_design = (
            f"### Study Design\n\n"
            f"{s.name}.{desc_clause} The cohort comprised "
            f"{s.n_samples} biological sample{'s' if s.n_samples != 1 else ''} "
            f"distributed across {len(s.group_levels)} treatment group"
            f"{'s' if len(s.group_levels) != 1 else ''}: {groups}. "
            f"The primary grouping variable was `{s.primary_group_col}`."
            f"{re_clause}{fe_clause}{ctrl_clause}\n"
        )

        sample_prep = (
            "### Sample Collection and Sequencing\n\n"
            "> TODO: sample-collection and sequencing protocol details are not "
            "captured in `manifest.json`. The human author must fill this in.\n"
        )

        # Bioinformatic pipeline — name, repo, version, completed stages.
        complete = [
            name
            for name, stage in self.manifest.stages.items()
            if stage.status == "complete"
        ]
        complete_clause = (
            f" The following analysis stages completed successfully: "
            f"{_join([f'`{x}`' for x in complete])}."
            if complete
            else ""
        )
        # Run timestamps are intentionally not embedded here — they are
        # provenance, not quantitative methods claims, and would pull
        # date/time integers into the prose that the no-invented-numbers
        # guardrail can't tell apart from real measurements. Provenance lives
        # in the pipeline version + repo URL.
        bioinformatic_lines = [
            "### Bioinformatic Pipeline",
            "",
            f"Sequencing reads were processed using **{p.name}** "
            f"(version `{p.version}`; {p.repo}).{complete_clause}",
        ]
        if self.methodology is not None:
            bioinformatic_lines += [
                "",
                self.methodology.pipeline.overview.strip(),
            ]
            # One bold-lead-in paragraph per completed stage, in manifest
            # iteration order so the prose follows the same order the
            # pipeline declares its stages.
            for stage_name in complete:
                entry = self.methodology.for_stage(stage_name)
                if entry is None:
                    continue
                bioinformatic_lines += [
                    "",
                    f"**{entry.title}.** {entry.prose.strip()}",
                ]
        bioinformatic = "\n".join(bioinformatic_lines) + "\n"

        # Statistical analysis — filters, ALDEx2 settings, PERMANOVA, alpha.
        statistical = (
            f"### Statistical Analysis\n\n"
            f"Samples were retained for analysis if they contained at least "
            f"{f.min_count_per_sample} reads. A species was retained if it was "
            f"supported by at least {f.min_count_for_species} reads in total. "
            f"Differential-abundance analysis used ALDEx2 with "
            f"{st.aldex_mc_samples} Monte Carlo samples; features were tested "
            f"only when they appeared in at least {f.min_prevalence_aldex} "
            f"samples. Compositional differences in community structure were "
            f"assessed with PERMANOVA using {st.permanova_permutations:,} "
            f"permutations. All significance tests used a threshold of "
            f"alpha = {st.alpha}.\n"
        )

        return (
            "## Materials and Methods\n\n"
            + study_design
            + "\n"
            + sample_prep
            + "\n"
            + bioinformatic
            + "\n"
            + statistical
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _panel_composites(self) -> list:
        """Every panel composite Figure in the manifest, in emission order."""
        stage = self.manifest.stages.get("panels")
        if stage is None:
            return []
        return [f for f in stage.figures if f.kind == "panel_composite"]

    def _find_results_section(self) -> Section | None:
        for section in self.journal.manuscript.sections:
            if section.id == "results":
                return section
        return None

    def _composites_by_subsection(self) -> dict[str, list[Figure]]:
        grouped: dict[str, list[Figure]] = {}
        for fig in self._panel_composites():
            if fig.subsection:
                grouped.setdefault(fig.subsection, []).append(fig)
        return grouped

    @staticmethod
    def _human_heading(slug: str) -> str:
        return slug.replace("_", " ").title()


def _composite_extension(fig: Figure) -> str:
    """Pick the file extension for a panel composite the same way FigureBuilder
    does (path suffix wins, format is the fallback). Keeps the drafter's
    embedded image paths in lockstep with the files FigureBuilder writes."""
    suffix = Path(fig.path).suffix.lstrip(".").lower() if fig.path else ""
    if suffix:
        return suffix
    if fig.format:
        return fig.format.lower()
    return "tiff"


def _join(items: list[str]) -> str:
    """Oxford-comma join for a list of human-readable items."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"
