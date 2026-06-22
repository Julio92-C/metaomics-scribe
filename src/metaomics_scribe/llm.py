"""LLM-driven results subsection drafter.

v0.4.1 fills the Results section of the manuscript by calling Claude once per
declared subsection. The model is asked to *select* claims from a fixed
grounding pack (manifest + parsed stats) — never to invent numbers — and a
post-hoc guardrail rejects any numeric token in the returned prose that
doesn't appear verbatim in the grounding pack.

Transport: the optional ``[llm]`` extra installs the **Claude Agent SDK**
(``claude-agent-sdk``), which routes calls through the user's logged-in
``claude`` CLI rather than the Anthropic REST API. This means subscription
auth (Pro/Max) works as-is — no ``ANTHROPIC_API_KEY`` required. The user
must have run ``claude`` interactively at least once to complete the OAuth
flow. The async ``query()`` call is run inside a dedicated worker thread so
this module stays usable from any caller (Streamlit, scripts, etc.) without
imposing async on the rest of the codebase.

Design choices:

- The Claude Agent SDK is an optional dependency. Importing this module never
  imports ``claude_agent_sdk``; the import happens lazily inside
  :func:`default_caller` so the deterministic ``ManuscriptDrafter.draft()``
  path keeps working without it installed.
- The caller is abstracted as a :data:`LLMCaller` (a plain
  ``(system, user, model) -> str`` callable). Tests pass a lambda; production
  calls use :func:`default_caller`. This lets us swap transports later without
  rippling through every test.
- The numeric-traceability guardrail (:func:`validate_no_invented_numbers`)
  re-uses the same regex/threshold as ``test_no_invented_numbers_in_methods``.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from .journal import Subsection
from .manifest import Figure, Manifest
from .stats_extractor import StatsBundle

DEFAULT_MODEL = "claude-opus-4-7"
"""Default Claude model for results drafting.

Selected for scientific-prose quality. The Claude Agent SDK accepts any model
your subscription has access to; pass ``--model claude-sonnet-4-6`` to the CLI
if Opus isn't on your plan.
"""


# A pure ``(system_prompt, user_prompt, model) -> response_text`` callable.
# Production = ``default_caller()`` (claude-agent-sdk). Tests = lambda.
LLMCaller = Callable[[str, str, str], str]


class ResultsGuardrailError(ValueError):
    """The LLM produced numeric tokens not present in the grounding pack.

    Carries the subsection id and the offending tokens so the caller can
    surface a precise failure to the human author rather than silently
    accepting fabricated numbers — the same invariant the methods section
    enforces by construction.
    """

    def __init__(self, subsection_id: str, illegal_tokens: list[str]):
        self.subsection_id = subsection_id
        self.illegal_tokens = illegal_tokens
        super().__init__(
            f"results subsection {subsection_id!r} contains numeric tokens not "
            f"present in the grounding pack: {illegal_tokens}"
        )


SYSTEM_INSTRUCTIONS = """\
You are drafting one Results subsection of a Frontiers in Microbiology
"Original Research" manuscript reporting a metagenomics study.

Hard constraints (read carefully — violations fail the build):

1. **No invented numbers.** Every numeric value in your prose MUST appear
   verbatim in the GROUNDING PACK below (manifest, parsed statistics, or
   raw stats text). If you can't find a number to support a claim, omit the
   claim — do not interpolate, round, or estimate.
2. **Reference figures by slot id in backticks.** Use the exact slot id
   shown in the subsection brief, e.g. ``fig01_taxa_overview``. Do not
   invent figure numbers like "Figure 1"; the journal will assign those at
   typesetting.
3. **No section heading.** The drafter prepends the heading; start directly
   with prose.
4. **Scientific past tense.** 150-250 words. One or two short paragraphs.
   Mention every figure the brief lists at least once.
5. **Cite p-values and effect sizes from the parsed statistics block only.**
   Quote them with the same precision the grounding pack uses.
6. **Disambiguate when multiple tests of the same kind exist.** Each parsed
   PERMANOVA / PERMDISP entry carries a ``source`` field naming the file it
   came from (e.g. ``permanova`` vs ``jaccard_permanova``). When a stage has
   more than one entry, either pick the right one for your claim AND name
   the distance metric explicitly, or quote both. Do NOT silently swap a
   Jaccard p-value into a sentence labelled "Bray-Curtis".

If the grounding pack contains no statistics for the metric you would
otherwise discuss, describe the visual pattern (from the figure caption
seed) without attaching a number to it.
"""


def build_grounding_pack(manifest: Manifest, stats_bundle: StatsBundle) -> str:
    """Serialise the deterministic facts the LLM is allowed to use.

    Sorted-key JSON for deterministic output — the guardrail relies on
    membership testing against this string, so byte-stable formatting matters.
    """
    pack = {
        "study": manifest.study.model_dump(mode="json"),
        "config": manifest.config.model_dump(mode="json"),
        "pipeline": {
            "name": manifest.pipeline.name,
            "version": manifest.pipeline.version,
            "repo": manifest.pipeline.repo,
        },
        "stage_summaries": _summarise_stages(manifest),
        "parsed_statistics": stats_bundle.model_dump(mode="json"),
    }
    return json.dumps(pack, indent=2, sort_keys=True, default=str)


def _summarise_stages(manifest: Manifest) -> dict[str, dict]:
    """Per-stage table/figure descriptions for the LLM's spatial awareness."""
    out: dict[str, dict] = {}
    for stage_id, stage in manifest.stages.items():
        if stage.status != "complete":
            continue
        out[stage_id] = {
            "tables": [
                {"kind": t.kind, "row_count": t.row_count, "description": t.description}
                for t in stage.tables
            ],
            "figures": [
                {
                    "kind": f.kind,
                    "caption_seed": f.caption_seed,
                    "groups": f.groups,
                    "metric": f.metric,
                    "pair": f.pair,
                    "slot": f.slot,
                    "subsection": f.subsection,
                }
                for f in stage.figures
            ],
        }
    return out


def _build_user_prompt(subsection: Subsection, composites: list[Figure]) -> str:
    """Per-subsection user-turn payload."""
    lines = [
        f"Draft the Results subsection titled {subsection.title or subsection.id!r}.",
        "",
        "Figures assigned to this subsection (reference each by its slot id in",
        "backticks at least once):",
        "",
    ]
    if not composites:
        lines.append("  (no panel composites carry this subsection in the manifest)")
    for fig in composites:
        slot = fig.slot or "(unknown slot)"
        caption = (fig.caption_seed or "").strip() or "(no caption seed)"
        lines.append(f"- `{slot}` - {caption}")
    lines += [
        "",
        "Ground every numeric claim in the GROUNDING PACK in the system block.",
        "Return only the prose - no heading, no preamble.",
    ]
    return "\n".join(lines)


def draft_results_section(
    caller: LLMCaller,
    *,
    manifest: Manifest,
    stats_bundle: StatsBundle,
    subsection: Subsection,
    composites: list[Figure],
    model: str = DEFAULT_MODEL,
) -> str:
    """Draft one Results subsection and validate it against the grounding pack.

    ``caller`` is any ``(system, user, model) -> str`` callable; production
    code passes :func:`default_caller`, tests pass a lambda. Raises
    :class:`ResultsGuardrailError` when the model emits a numeric token that
    isn't in the grounding pack.
    """
    grounding = build_grounding_pack(manifest, stats_bundle)
    system_text = f"{SYSTEM_INSTRUCTIONS}\n\n## GROUNDING PACK\n\n```json\n{grounding}\n```\n"
    user_text = _build_user_prompt(subsection, composites)

    prose = caller(system_text, user_text, model).strip()
    validate_no_invented_numbers(prose, grounding, subsection.id)
    return prose


def default_caller() -> LLMCaller:
    """Build a synchronous caller backed by the Claude Agent SDK.

    The async ``query()`` is run inside a dedicated worker thread so callers
    don't need to manage an event loop — pattern borrowed from the
    ebreathomics agent's ``ask_agent`` (notes: avoids the Streamlit + Windows
    asyncio + subprocess hang).

    Raises :class:`RuntimeError` (not ``ImportError``) when the SDK isn't
    installed so the CLI can surface a single clear "install the extra"
    message regardless of failure mode.
    """
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
        from claude_agent_sdk.types import AssistantMessage, TextBlock
    except ImportError as exc:
        raise RuntimeError(
            "claude-agent-sdk is not installed. Install the optional `llm` "
            "extra with: uv sync --extra llm"
        ) from exc

    async def _ask_async(system_prompt_path: str, user_message: str, model: str) -> str:
        # ``allowed_tools=[]`` forces pure text generation. Without it the SDK
        # exposes Read/Write/Bash etc. to the model and it tries to use them,
        # leaking tool-attempt narration into the prose. The system prompt is
        # passed via file because our grounding pack JSON is far larger than
        # the Windows command-line argument limit (~8K chars).
        options = ClaudeAgentOptions(
            system_prompt={"type": "file", "path": system_prompt_path},
            model=model,
            allowed_tools=[],
        )
        chunks: list[str] = []
        async for message in query(prompt=user_message, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        chunks.append(block.text)
        return "".join(chunks)

    def _call(system_prompt: str, user_prompt: str, model: str) -> str:
        # Tempfile carries the system prompt so we never hit Windows' ~8K
        # command-line limit. ``delete=False`` because Windows can't open a
        # file twice while it's held open by the process that created it.
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            prefix="metaomics_scribe_system_",
            delete=False,
            encoding="utf-8",
        )
        try:
            tmp.write(system_prompt)
            tmp.close()
            system_prompt_path = tmp.name

            result_box: list[str] = []
            error_box: list[BaseException] = []

            def _runner() -> None:
                try:
                    result_box.append(
                        asyncio.run(_ask_async(system_prompt_path, user_prompt, model))
                    )
                except BaseException as e:
                    # Captured on the worker thread, re-raised on the calling thread.
                    error_box.append(e)

            worker = threading.Thread(
                target=_runner, name="metaomics_scribe_llm", daemon=True
            )
            worker.start()
            worker.join()
            if error_box:
                raise error_box[0]
            return result_box[0]
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    return _call


# The same regex/threshold as test_no_invented_numbers_in_methods — keep them
# in sync. We accept any token whose magnitude (commas stripped) is < 2 as
# grammar/ordinal, not a quantitative claim.
_NUMERIC_TOKEN_RE = re.compile(r"\d{1,3}(?:,\d{3})+|\d+\.\d+|\d+")


def validate_no_invented_numbers(
    prose: str, grounding: str, subsection_id: str
) -> None:
    """Raise :class:`ResultsGuardrailError` if ``prose`` contains a numeric
    token absent from ``grounding``.

    ``grounding`` is the JSON-serialised grounding pack — every value the LLM
    is allowed to quote appears there verbatim. Tokens with magnitude < 2 are
    skipped because the integers 0 and 1 appear constantly in grammar.
    """
    illegal: list[str] = []
    for token in _NUMERIC_TOKEN_RE.findall(prose):
        try:
            magnitude = float(token.replace(",", ""))
        except ValueError:
            continue
        if magnitude < 2:
            continue
        if token in grounding or token.replace(",", "") in grounding:
            continue
        illegal.append(token)
    if illegal:
        raise ResultsGuardrailError(subsection_id, illegal)
