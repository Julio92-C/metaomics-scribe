# `manifest.json` schema

The single contract between the upstream metagenomics pipeline and `metaomics-scribe`. The pipeline writes one `manifest.json` into the report directory (next to `pipeline_report.html`). The agent is pointed at this file (or its parent directory), reads it, and resolves every artifact path through it.

## Design goals

- **Stable.** Renaming a column in a pipeline-internal CSV must not break the agent. The agent reads paths and schemas from the manifest, not from convention.
- **Self-describing.** Every artifact has a `kind`, every table has a `schema`, every figure has a `metric`/`groups`/`pair` annotation. The agent should not have to guess what a file is from its filename.
- **Versioned.** The top-level `manifest_version` lets the agent reject manifests it doesn't understand and lets the pipeline evolve the contract without silent breakage.
- **Optional stages.** Any stage may be absent (study skipped it) or `"status": "failed"` (study ran it but it didn't produce usable output). The agent must handle both.

## Top-level structure

```jsonc
{
  "manifest_version": "1.0",
  "study": { ... },        // describes the experiment
  "config": { ... },       // pipeline configuration snapshot (filters, stats params)
  "outputs": { ... },      // base directories (paths in stages are relative to these)
  "stages": { ... },       // per-stage artifact inventory
  "pipeline": { ... }      // pipeline identity + run timing
}
```

Paths inside `stages` are **relative to `project_root`**, NOT to the manifest file itself. The manifest's own location (`<project_root>/<report_dir>/manifest.json`) is buried a few directories deep, so resolving every stage path against the manifest dir would mean every path starts with `../../`. Instead, the manifest declares a single `outputs.project_root` field — the relative walk from `manifest_dir` back to `project_root` — and the agent resolves paths as:

```
manifest_dir / outputs.project_root / stage_entry.path
```

This keeps stage paths short and human-readable (`Figures/alpha_diversity/shannon_violin.png`) while still being portable when the project is copied between machines.

## `study`

Captures everything needed to draft the methods and results sections without re-reading the metadata CSV.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Slug, matches the project dir name. |
| `name` | string | yes | Human title. |
| `description` | string | no | Multi-line study description from the config. |
| `primary_group_col` | string | yes | The grouping variable used in plots/stats (`Treatment_Bird`, etc.). |
| `random_effect` | string \| null | yes | Random effect column, `null` if none. |
| `fixed_effects` | string[] | yes | Fixed-effect columns. |
| `group_levels` | string[] | yes | Ordered factor levels for `primary_group_col`. |
| `n_samples` | integer | yes | Real samples (excludes controls). |
| `n_controls` | integer | yes | Number of negative controls in the rcf data. |
| `control_ids` | string[] | no | Control sample IDs (post any rename). |

## `config`

A flat snapshot of the parameters reviewers will want to see in methods. Not the full yaml — only the values that drive results.

```jsonc
{
  "filters": {
    "min_count_per_sample":  17,
    "min_count_for_species": 500,
    "min_prevalence_aldex":  2
  },
  "stats": {
    "permanova_permutations": 9999,
    "aldex_mc_samples":       128,
    "alpha":                  0.05
  }
}
```

## `outputs`

```jsonc
{
  "project_root": "../..",          // walk from manifest_dir back to project_root
  "datasets_dir": "test_run/Datasets",
  "figures_dir":  "test_run/Figures",
  "log_file":     "test_run/Reports/pipeline.log"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `project_root` | string | yes | Relative walk from the manifest's directory back to `project_root`. Typically `"../.."` when outputs live under `<project_root>/test_run/Reports/`. Use `"."` if the manifest sits at the project root. |
| `datasets_dir` | string | yes | Path (relative to `project_root`) where tabular outputs live. |
| `figures_dir` | string | yes | Path (relative to `project_root`) where figure files live. |
| `log_file` | string | no | Path to the pipeline's run log. |

The agent resolves every stage `path` as `manifest_dir / outputs.project_root / stage_entry.path`. The `datasets_dir` / `figures_dir` fields are informational — they help the agent sanity-check that stage paths start with the expected prefixes but are not strictly required for resolution.

## `stages`

A map keyed by stage name. Stage names mirror the pipeline's R/NN_*.R module names without the leading number. Expected keys (any may be absent):

```
clean_data, normalisation, taxonomy, relative_abundance,
alpha_diversity, beta_diversity, differential_abundance,
resistome, virulome, mobilome, network
```

Each stage entry has the shape:

```jsonc
{
  "status":     "complete" | "skipped" | "failed",
  "duration_s": 12.3,
  "tables":     [ ... ],
  "figures":    [ ... ],
  "stats_text": [ ... ]    // optional: PERMANOVA/KW txt files etc.
}
```

### `tables` entries

```jsonc
{
  "path":        "Datasets/alpha_diversity.csv",
  "kind":        "alpha_diversity_per_sample",
  "format":      "csv",
  "schema": {
    "sample":                  "string",
    "shannon_diversity_index": "double",
    "richness":                "double",
    "simpson_index":           "double",
    "...":                     "..."
  },
  "row_count":   34,
  "description": "Per-sample alpha-diversity indices, one row per sample."
}
```

`kind` is a controlled vocabulary the agent maps to manuscript sections (see "Kind vocabulary" below). `schema` is a flat name→type map; complex types (factor levels, units) live in `description`.

### `figures` entries

```jsonc
{
  "path":        "Figures/alpha_diversity/shannon_diversity_index_violin.png",
  "kind":        "alpha_violin",
  "metric":      "Shannon diversity index",
  "groups":      ["Control_W4", "Control_W5", "Dulse_W4", "Dulse_W5"],
  "width_px":    1800,
  "height_px":   1200,
  "dpi":         300,
  "caption_seed": "Shannon diversity across the four Treatment x Time groups; violin shows the distribution, inner box the IQR."
}
```

- `kind` again maps to manuscript usage (violin, bar, heatmap, pcoa, volcano, network, chord, sankey).
- `metric` / `groups` / `pair` annotate the figure with the dimension that varies, so the agent can pick the right one for a given results paragraph without parsing filenames.
- `caption_seed` is a short pipeline-written description the agent expands into a journal-formatted caption.

### `stats_text` entries

Plain-text artifacts (PERMANOVA, PERMDISP, Kruskal-Wallis output). The agent parses these for quoted statistics in the results section.

```jsonc
{
  "path":        "Figures/beta_diversity/permanova.txt",
  "kind":        "permanova",
  "primary_var": "Treatment_Bird",
  "description": "Adonis PERMANOVA result for the primary grouping."
}
```

## `pipeline`

Provenance — never inferred, always emitted.

```jsonc
{
  "name":           "Metagenomics_pipeline_automation",
  "repo":           "https://github.com/Julio92-C/Metagenomics_pipeline_automation",
  "version":        "189f174",    // git SHA at run time
  "run_started_at":  "2026-05-30T13:45:59+00:00",
  "run_finished_at": "2026-05-30T13:52:42+00:00"
}
```

## `kind` vocabulary

Stable strings the agent uses to route artifacts to manuscript sections. Add new entries here when a new artifact type is introduced; never overload existing kinds.

### Tables

| `kind` | Produced by | Used by agent for |
|---|---|---|
| `composition_long` | relative_abundance | Results: composition |
| `alpha_diversity_per_sample` | alpha_diversity | Results: alpha diversity |
| `beta_pcoa_scores` | beta_diversity | Results: beta diversity |
| `daa_pairwise` | differential_abundance | Results: DAA, supplementary tables |
| `daa_overall_kw` | differential_abundance | Results: overall test summary |
| `daa_top_candidates` | differential_abundance | Results: top hits table |
| `ge_alpha_diversity` | resistome / virulome / mobilome | Results: domain alpha |
| `ge_tpm_totals` | resistome / virulome / mobilome | Results: domain abundance |
| `network_nodes` / `network_edges` | network | Methods + supplementary |
| `network_topology` | network | Results: network topology |

### Figures

| `kind` | Produced by |
|---|---|
| `venn` | taxonomy |
| `taxa_heatmap` | taxonomy |
| `composition_stacked_bar` | relative_abundance |
| `species_count` / `species_prevalence` | relative_abundance |
| `alpha_violin` / `alpha_bar` | alpha_diversity |
| `pcoa_scatter` | beta_diversity |
| `aldex_plot` / `volcano` / `top_candidates` | differential_abundance |
| `ge_*` (per-domain variants) | resistome / virulome / mobilome |
| `network_graph` / `chord` / `sankey` / `degree_distribution` | network |

### Stats text

| `kind` | Produced by |
|---|---|
| `permanova` / `permdisp` | beta_diversity, ge stages |
| `kruskal_wallis` | alpha_diversity, ge stages |
| `alpha_stats` | alpha_diversity |

## Versioning

- `manifest_version` is semver-string: `MAJOR.MINOR`.
- Adding a new optional field or a new `kind` is **MINOR**.
- Renaming a field, changing a field's type, or removing a `kind` is **MAJOR**.
- The agent refuses to run against a `MAJOR` it does not understand.

## Validation

A JSON Schema (Draft 2020-12) will live at `docs/manifest.schema.json` once the contract is stable. Until then this markdown is the source of truth.
