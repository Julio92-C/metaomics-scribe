# metaomics-scribe

A research agent that turns a completed metagenomics analysis into a journal-ready manuscript draft — figures, tables, and prose grounded in the actual numbers produced upstream.

**Status.** Scaffolding only. This repository contains the contract, the layout, and the journal templates. No agent code yet. The intent is to lock the inputs/outputs first, then build incrementally.

## What it will do

- Read a `manifest.json` emitted by an upstream pipeline (initial target: [Metagenomics_pipeline_automation](https://github.com/Julio92-C/Metagenomics_pipeline_automation), but the contract is pipeline-agnostic).
- Pick a journal template (figure sizes, section structure, citation style, word/figure caps).
- Compose multi-panel publication figures from the pipeline's per-stage PNGs (taxonomy, alpha/beta diversity, differential abundance, AMR/virulence/MGE, network).
- Draft each manuscript section (abstract / introduction / methods / results / discussion / conclusion / references), grounding every quantitative claim on a value from the manifest so numbers cannot drift from the actual analysis.
- Emit a main manuscript and a supplementary document, each as both `.docx` and `.tex` where the journal supports it.

## What it will NOT do

- Run the upstream pipeline. The agent assumes the pipeline ran successfully and emitted a manifest.
- Invent results. Every numeric claim in the draft must be traceable to a manifest entry; sections that lack the necessary data should be flagged for the human author rather than filled with plausible-sounding prose.
- Replace human judgement on novelty, framing, or scope. The draft is a starting point.

## The contract: `manifest.json`

The upstream pipeline writes a single `manifest.json` next to its other outputs. This file is the only thing the agent reads from the pipeline — paths, schemas, and figure inventories all live here. Coupling the two repos through this stable JSON keeps the pipeline free to rename internal paths without breaking the agent.

See [`docs/MANIFEST_SCHEMA.md`](docs/MANIFEST_SCHEMA.md) for the full spec, and [`examples/manifest.example.json`](examples/manifest.example.json) for a worked example from the chicken_batch2 run.

## Journal templates

Each journal lives in `journals/<id>.yaml` and declares its constraints: figure dimensions, citation style, IMRaD section layout, word caps, supplementary numbering. See [`journals/frontiers_microbiome.yaml`](journals/frontiers_microbiome.yaml) for the reference template. Adding a new journal is a config change, not a code change.

## Layout

```
metaomics-scribe/
├── README.md                          (this file)
├── LICENSE                            MIT
├── CITATION.cff                       cite the tool itself
├── docs/
│   └── MANIFEST_SCHEMA.md             full contract spec
├── examples/
│   └── manifest.example.json          chicken_batch2 worked example
└── journals/
    └── frontiers_microbiome.yaml      reference journal template
```

When implementation starts, `src/metaomics_scribe/` (Python package) and `tests/` will be added.

## Getting started

The repo is pre-implementation: there is nothing to install or run yet. What you *can* do today:

```bash
git clone https://github.com/Julio92-C/metaomics-scribe.git
cd metaomics-scribe
```

- Read [`docs/MANIFEST_SCHEMA.md`](docs/MANIFEST_SCHEMA.md) — the full contract spec, the only thing the agent will read from an upstream pipeline.
- Inspect [`examples/manifest.example.json`](examples/manifest.example.json) — a real `chicken_batch2` manifest, exercising every stage and `kind` in the vocabulary.
- Review [`journals/frontiers_microbiome.yaml`](journals/frontiers_microbiome.yaml) — the reference journal template; adding a new journal will be a config edit, not a code change.

Once **v0.1** lands (see Roadmap), the Python package will be installable via [`uv`](https://docs.astral.sh/uv/):

```bash
uv sync          # create venv and install deps from pyproject.toml
uv run pytest    # run the test suite
```

Issues and discussion: <https://github.com/Julio92-C/metaomics-scribe/issues>.

## Roadmap

1. **v0.1 — contract freeze.** Manifest schema stabilised, one journal template, one example manifest from a real run.
2. **v0.2 — figure builder.** Compose multi-panel main and supplementary figures from pipeline PNGs, honouring journal dimensions and DPI.
3. **v0.3 — methods + results drafters.** The two sections most directly grounded in the manifest — methods from config, results from per-stage stats files.
4. **v0.4 — introduction + discussion drafters.** Higher-judgement sections; ground in cited literature plus the manifest's headline numbers.
5. **v0.5 — full IMRaD + supplementary pass.** End-to-end run on chicken_batch2, reviewed by the human author.
6. **v1.0 — second journal template + second study.** Demonstrates the journal-agnostic and pipeline-agnostic claims.

## Upstream pipeline integration

The upstream pipeline needs to write `manifest.json` at the end of its `report` stage. The minimum required fields are listed in `docs/MANIFEST_SCHEMA.md`. Once the contract is locked, the pipeline's `R/13_manifest.R` (or equivalent) becomes a small writer module — most of the data it needs is already in `cfg` and the per-stage output dirs.

## License & citation

MIT. See `LICENSE` and `CITATION.cff`.
