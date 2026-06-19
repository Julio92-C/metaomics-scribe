# Pipeline — gap to close for manifest schema 1.3

Single outstanding gap on the
[`Metagenomics_pipeline_automation`](https://github.com/Julio92-C/Metagenomics_pipeline_automation)
side so `metaomics-scribe` can route the manuscript composites without
ad-hoc directory globbing.

## Context

The pipeline already:

- Composes every multi-panel main and supplementary figure as a single TIFF
  with panel labels (A/B/C…) baked in.
- Writes them to `<project_root>/test_run/Figures/panels/main/*.tiff` and
  `.../panels/supplementary/*.tiff`.
- Emits `manifest_version: "1.2"` with 169 per-panel entries inside the
  `stages.*.figures` arrays (used for traceability, not for figure routing).

The agent's `FigureBuilder` was rewritten (June 2026) into a pure router: it
no longer stitches anything. Instead it expects to read each manuscript slot
from a new top-level `panels` block in `manifest.json` and copy the TIFF
verbatim into `runs/<study_id>/figures/<slot_id>.tiff`.

The new schema is documented in `docs/MANIFEST_SCHEMA.md` §`panels` and
§Changelog 1.3.

## What needs to change in the pipeline

Add a `panels` block to the manifest writer (`R/13_manifest.R` or wherever
manifest assembly lives), bump `manifest_version` to `"1.3"`, and emit
**one entry per file** under `Figures/panels/main/` and
`Figures/panels/supplementary/`.

```jsonc
"panels": {
  "main": [
    {
      "id":           "fig01_taxa_overview",
      "path":         "test_run/Figures/panels/main/fig01_taxa_overview.tiff",
      "format":       "tiff",
      "caption_seed": "Taxonomic overview: alpha diversity (A), beta diversity (B), composition (C)."
    },
    { "id": "fig02_resistome_overview",  "path": "test_run/Figures/panels/main/fig02_resistome_overview.tiff",  "format": "tiff", "caption_seed": "…" },
    { "id": "fig03_virulome_overview",   "path": "test_run/Figures/panels/main/fig03_virulome_overview.tiff",   "format": "tiff", "caption_seed": "…" },
    { "id": "fig04_mobilome_overview",   "path": "test_run/Figures/panels/main/fig04_mobilome_overview.tiff",   "format": "tiff", "caption_seed": "…" },
    { "id": "fig05_relative_abundance_species", "path": "test_run/Figures/panels/main/fig05_relative_abundance_species.tiff", "format": "tiff", "caption_seed": "…" },
    { "id": "fig06_chord",               "path": "test_run/Figures/panels/main/fig06_chord.tiff",               "format": "tiff", "caption_seed": "…" },
    { "id": "fig07_sankey_vf",           "path": "test_run/Figures/panels/main/fig07_sankey_vf.tiff",           "format": "tiff", "caption_seed": "…" },
    { "id": "fig09_network",             "path": "test_run/Figures/panels/main/fig09_network.tiff",             "format": "tiff", "caption_seed": "…" },
    { "id": "fig10_vf_arg_correlation",  "path": "test_run/Figures/panels/main/fig10_vf_arg_correlation.tiff",  "format": "tiff", "caption_seed": "…" }
  ],
  "supplementary": [
    { "id": "figS00_heatmap_species",              "path": "test_run/Figures/panels/supplementary/figS00_heatmap_species.tiff",              "format": "tiff", "caption_seed": "…" },
    { "id": "figS00b_network_degree_distribution", "path": "test_run/Figures/panels/supplementary/figS00b_network_degree_distribution.tiff", "format": "tiff", "caption_seed": "…" },
    { "id": "figS00c_rarefaction_curves",          "path": "test_run/Figures/panels/supplementary/figS00c_rarefaction_curves.tiff",          "format": "tiff", "caption_seed": "…" },
    { "id": "figS01_heatmap_arg_genes",            "path": "test_run/Figures/panels/supplementary/figS01_heatmap_arg_genes.tiff",            "format": "tiff", "caption_seed": "…" },
    { "id": "figS02_heatmap_vf_genes",             "path": "test_run/Figures/panels/supplementary/figS02_heatmap_vf_genes.tiff",             "format": "tiff", "caption_seed": "…" },
    { "id": "figS_circos_chord_by_treatment",      "path": "test_run/Figures/panels/supplementary/figS_circos_chord_by_treatment.tiff",      "format": "tiff", "caption_seed": "…" },
    { "id": "figS_diet_effects_arg",               "path": "test_run/Figures/panels/supplementary/figS_diet_effects_arg.tiff",               "format": "tiff", "caption_seed": "…" },
    { "id": "figS_diet_effects_mge",               "path": "test_run/Figures/panels/supplementary/figS_diet_effects_mge.tiff",               "format": "tiff", "caption_seed": "…" },
    { "id": "figS_diet_effects_vf",                "path": "test_run/Figures/panels/supplementary/figS_diet_effects_vf.tiff",                "format": "tiff", "caption_seed": "…" }
  ]
}
```

### Contract rules

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Matches the manuscript slot id in the journal template (`journals/frontiers_microbiome.yaml`). Drives the output filename. |
| `path` | yes | Relative to `outputs.project_root`, same rule as stage paths. |
| `format` | no | Inferred from the path suffix when absent. Use `tiff` for Frontiers; `png` is also accepted. |
| `caption_seed` | no but recommended | Short description (1–2 sentences) of what each panel shows. The agent prepends the journal-side title and writes the sidecar caption from it. |

### Slot id convention

Filenames in `Figures/panels/main/` and `.../supplementary/` already follow
`<slot_id>.tiff` — keep emitting them that way. The agent expects the manifest
`id` to be the basename without the extension. fig08 is intentionally absent
(no fig08 composite is produced); the journal template skips fig08 too.

### Validation

Once the pipeline emits the block, this should succeed against the run:

```powershell
uv run pytest -k real_chicken --tb=short -s
# with METAOMICS_REAL_FIXTURES=<path-to-project> exported
```

The router will copy each `panels[*].path` into
`runs/<study_id>/figures/<slot_id>.<ext>` with a `<slot_id>.caption.txt`
sidecar — that's the artifact the manuscript drafter will consume in the next
agent milestone.

## Why this replaces the old per-`kind` punch-list

The per-`kind` gap-closing work tracked in the deleted `PIPELINE_V1.1_GAPS.md`
and in earlier drafts of this file targeted a stitching agent that no longer
exists. The pipeline composes the manuscript figures itself, so the agent
only needs slot ids and file paths. The full `kind` vocabulary is still
useful for the per-panel entries in `stages.*.figures` (for traceability and
data-availability statements), but it is not part of the figure-routing
contract anymore.
