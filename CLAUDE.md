# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

Scaffolding only. There is no agent code, no `src/`, no `tests/`, no build tooling yet. The repo currently contains three artifacts:

- `docs/MANIFEST_SCHEMA.md` — the contract spec
- `examples/manifest.example.json` — a worked example (chicken_batch2 run)
- `journals/frontiers_microbiome.yaml` — reference journal template

When implementation begins, the Python package goes in `src/metaomics_scribe/` and tests in `tests/` (per README §Layout). Until then, work on this repo means editing the contract, templates, and docs — not writing application code.

## Architectural intent (read before changing the contract)

The whole repo is organised around one stable boundary: `manifest.json`. The upstream metagenomics pipeline writes it; this agent reads only it. **Three invariants flow from that choice and constrain every change:**

1. **The agent reads paths and schemas from the manifest, never from convention.** If you find yourself adding code that hardcodes a filename, column name, or directory layout from the pipeline side, you are leaking the contract. Add a field to the manifest instead.

2. **Path resolution is `manifest_dir / outputs.project_root / stage_entry.path`** — not `manifest_dir / stage_entry.path`. The manifest sits a few directories deep inside the project; `outputs.project_root` is the relative walk back up. Stage paths are relative to the project root so they stay short and human-readable. See `docs/MANIFEST_SCHEMA.md` §`outputs` for the rationale.

3. **No invented numbers.** Every quantitative claim in a drafted manuscript section must be traceable to a manifest entry (a table row, a stats-text file, a config value). When a stage is `"skipped"` or `"failed"`, the corresponding manuscript section must flag the gap for the human author — never fabricate plausible-sounding values.

## The `kind` vocabulary

`kind` strings (`alpha_violin`, `composition_long`, `permanova`, …) are how the manifest tells the agent what an artifact is *for*, independent of filename. They are a controlled vocabulary, not free-form tags.

- **Adding a new `kind` is a MINOR version bump.** Add a row to the table in `docs/MANIFEST_SCHEMA.md` §"`kind` vocabulary" and update affected journal templates.
- **Never overload an existing `kind`** to mean something new. If a new artifact type appears, give it a new name.
- **Renaming or removing a `kind` is a MAJOR version bump** and the agent must refuse manifests it doesn't understand.

## Journal templates

Each journal lives in `journals/<id>.yaml` as pure config: figure dimensions, citation style, IMRaD section order, word caps, figure-slot → `kind` mapping. **Adding a journal is a config change, not a code change** — if you find yourself wanting to add per-journal logic to a future code module, push it back into the YAML instead.

The `figure_slots` section maps manuscript figure positions to one-or-more manifest figure `kind`s. If a kind is absent from a given run (stage skipped), the slot reflows rather than failing.

## Manifest versioning rules

From `docs/MANIFEST_SCHEMA.md` §Versioning, restated because it governs every contract edit:

- `manifest_version` is `MAJOR.MINOR`.
- **MINOR:** adding a new optional field, adding a new `kind`.
- **MAJOR:** renaming a field, changing a field's type, removing a `kind`.
- The agent refuses to run against a `MAJOR` it does not understand.

When proposing a contract change, state which bump it is. Silent breakage of downstream agent code is the failure mode this contract exists to prevent.

## Upstream coupling

The pipeline this agent consumes is [Metagenomics_pipeline_automation](https://github.com/Julio92-C/Metagenomics_pipeline_automation), but the contract is deliberately pipeline-agnostic. Any pipeline that emits a conforming `manifest.json` should work. When discussing changes that touch both sides, treat the manifest schema as the source of truth and the pipeline's writer module (`R/13_manifest.R` or equivalent) as the implementation detail.
