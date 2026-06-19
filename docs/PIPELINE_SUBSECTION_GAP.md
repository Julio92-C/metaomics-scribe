# Pipeline gap — `Figure.subsection` on panel composites

Single follow-on gap on the
[`Metagenomics_pipeline_automation`](https://github.com/Julio92-C/Metagenomics_pipeline_automation)
side now that the journal template has been generalised (study-agnostic; no
slot enumeration, no per-subsection slot pins).

## Context

The journal template (`journals/frontiers_microbiome.yaml`) now only carries
journal style + IMRaD structure. It does **not** list figure slot ids and
does **not** pin slots to results subsections.

The drafter still needs to know *which* results subsection each composite
belongs to, in order to write the right paragraph beneath the right figure.
That mapping is the pipeline's job — the pipeline knows what each composite
shows because it generated the underlying analysis.

## What needs to change in the pipeline

Add an optional `subsection` field on each `panel_composite` figure emitted by
the `panels` stage. The value matches a `Subsection.id` declared by the
journal template (e.g. `"resistome"`, `"virulome"`).

```jsonc
"figures": [
  {
    "path":         "test_run/Figures/panels/main/fig01_taxa_overview.tiff",
    "kind":         "panel_composite",
    "format":       "tiff",
    "section":      "main",
    "subsection":   "community_overview",   // ← new
    "slot":         "fig01_taxa_overview",
    "caption_seed": "Taxonomic overview: alpha (A), beta (B), composition (C)."
  },
  …
]
```

### Suggested mapping for the current Frontiers Microbiology layout

| Slot | Section | Subsection |
|---|---|---|
| `fig01_taxa_overview` | main | `community_overview` |
| `fig05_relative_abundance_species` | main | `community_overview` |
| `fig02_resistome_overview` | main | `resistome` |
| `fig03_virulome_overview` | main | `virulome` |
| `fig04_mobilome_overview` | main | `mobilome` |
| `fig06_chord` | main | `taxa_gene_associations` |
| `fig07_sankey_vf` | main | `taxa_gene_associations` |
| `fig09_network` | main | `co_occurrence_network` |
| `fig10_vf_arg_correlation` | main | `co_occurrence_network` |
| `figS00_heatmap_species` | supplementary | `community_overview` |
| `figS00b_network_degree_distribution` | supplementary | `co_occurrence_network` |
| `figS00c_rarefaction_curves` | supplementary | `community_overview` |
| `figS01_heatmap_arg_genes` | supplementary | `resistome` |
| `figS02_heatmap_vf_genes` | supplementary | `virulome` |
| `figS_circos_chord_by_treatment` | supplementary | `taxa_gene_associations` |
| `figS_diet_effects_arg` | supplementary | `resistome` |
| `figS_diet_effects_mge` | supplementary | `mobilome` |
| `figS_diet_effects_vf` | supplementary | `virulome` |

Mapping is for a Frontiers Microbiology run; other journals or other
manuscripts can use a different subsection vocabulary (any string matching a
`Subsection.id` in the chosen journal template).

## Backwards compatibility

`subsection` is **optional**. Composites without it are collected by
`FigureBuilder.group_by_subsection()` under the `None` bucket; the drafter
flags those as needing a manual assignment. The agent doesn't refuse to run
against older manifests, so the pipeline can ship this incrementally — start
with whatever subset of figures has a clean subsection mapping and fill in
the rest later.

## Validation

```powershell
uv run pytest -k real_chicken --tb=short -s
# with METAOMICS_REAL_FIXTURES=<path-to-project> exported
```

Once the field is emitted, expect
`FigureBuilder.group_by_subsection(fb.build_all())` to return six populated
keys (one per results subsection in the Frontiers template) and an empty
`None` bucket.
