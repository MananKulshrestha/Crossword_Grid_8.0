# judge_runner

Scores `eval_runner/results/*_results.json` against expected outputs. Run: `python3 judge_eval.py`.

## Scoring

**Attribute extraction** — per query, compare `expected_output.extracted_attributes` vs the model's parsed `extracted_attributes`, key by key.

- Match (TP): key present in both.
- Miss (FN): key expected, not predicted.
- Extra/wrong (FP): key predicted, not expected — also counts as FN if the value was wrong (key exists on both sides but values differ: counted as both FP and FN).
- **Strict**: value must be exactly equal after normalization (casefold, whitespace/dash/underscore collapsed).
- **Loose**: value matches if one is a substring of the other (e.g. `"under $80"` vs `"$80"`, `"size 10"` vs `"10"`).

Both are computed and reported side by side (`attribute_metrics` / `attribute_metrics_loose`); strict is the ground truth, loose shows how much of the gap is formatting vs actually wrong values.

**Missing-attribute detection** — compares `missing_metadata` entries (attribute + priority + status) as exact-match sets only. No loose mode — these are categorical, not free text.

**Missing exact-set** — % of samples where the full predicted `missing_metadata` set exactly equals expected.

## Output

- `logs/<model>_evaluation_report.json` — per-sample scoring detail (strict + loose).
- `logs/<model>_attribute_verdicts.jsonl` — same, streamed live during scoring.
- Console table — micro P/R/F1 per model, strict and loose side by side.
