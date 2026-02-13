# CIRE-RAG Visual Anchor Benchmark Report

Generated at: `2026-02-11T18:57:32.497324+00:00`

## Aggregate Metrics

| Pipeline | Cases | Context Recall | Faithfulness | Avg Latency (s) | Total Cost (USD) |
|---|---:|---:|---:|---:|---:|
| baseline_text | 2 | 0.500 | 0.200 | 0.000 | 0.0200 |
| visual_anchor | 2 | 1.000 | 0.333 | 0.000 | 0.0400 |

## A/B Delta

- Delta Score (Context Recall): `+50.00%`
- Delta Score (Faithfulness): `+13.33%`
- Delta Cost: `$+0.0200`

## Warnings / Suggestions - baseline_text
- Sugerencia: Ajustar System Prompt del Generador para ser mas literal.
- Sugerencia: Mejorar la descripcion semantica (summary) generada por el VLM en la ingesta.

## Warnings / Suggestions - visual_anchor
- Sugerencia: Ajustar System Prompt del Generador para ser mas literal.

## Per-Case Detail

### visual_anchor

| Case | Context Recall | Faithfulness | Latency (s) | Cost (USD) |
|---|---:|---:|---:|---:|
| iso_table_b1_integrity | 1.000 | 0.333 | 0.000 | 0.0300 |
| iso_clause_8_4_textual | 1.000 | 0.333 | 0.000 | 0.0100 |

### baseline_text

| Case | Context Recall | Faithfulness | Latency (s) | Cost (USD) |
|---|---:|---:|---:|---:|
| iso_table_b1_integrity | 0.000 | 0.200 | 0.000 | 0.0100 |
| iso_clause_8_4_textual | 1.000 | 0.200 | 0.000 | 0.0100 |
