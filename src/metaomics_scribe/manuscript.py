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
from dataclasses import dataclass
from pathlib import Path

from .journal import Journal, Section
from .manifest import Manifest
from .methodology import Methodology


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
        cmd = ["pandoc", str(drafted.markdown_path), "-o", str(docx_path)]
        if reference_doc is not None:
            cmd += [f"--reference-doc={reference_doc}"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
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
        """Results stub that already references the routed composites under each
        subsection so the human author sees the figure placement up front."""
        heading = self._human_heading(section.id)
        lines = [f"## {heading}", ""]
        if not section.subsections:
            lines.append("> TODO: results subsections not declared in the journal.")
            return "\n".join(lines) + "\n"

        # Group every panel composite by subsection (or None when absent).
        groups: dict[str | None, list[str]] = {}
        for fig in self._panel_composites():
            groups.setdefault(fig.subsection, []).append(fig.slot or "(no slot)")

        for sub in section.subsections:
            sub_heading = sub.title or self._human_heading(sub.id)
            lines += [f"### {sub_heading}", ""]
            slots = groups.get(sub.id, [])
            if slots:
                lines.append(
                    "Figures in this subsection: " + ", ".join(f"`{s}`" for s in slots) + "."
                )
            else:
                lines.append(
                    f"> TODO: no `panel_composite` carries `subsection: {sub.id}` in the manifest."
                )
            lines.append("")
            lines.append(f"> TODO: prose for {sub.id} not yet drafted by v0.4.0.")
            lines.append("")

        unassigned = groups.get(None, [])
        if unassigned:
            lines += [
                "### Figures awaiting subsection assignment",
                "",
                "> The pipeline did not emit a `subsection` field on the following "
                "composites — the human author must place them into a results "
                "subsection above:",
                "",
                *[f"- `{s}`" for s in unassigned],
                "",
            ]
        return "\n".join(lines) + "\n"

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

    @staticmethod
    def _human_heading(slug: str) -> str:
        return slug.replace("_", " ").title()


def _join(items: list[str]) -> str:
    """Oxford-comma join for a list of human-readable items."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"
