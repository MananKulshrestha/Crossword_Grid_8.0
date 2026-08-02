## Current Judge Contract

`judge_runner/judge_eval.py` is a deterministic evaluator, not an LLM judge;
there is therefore no active judge system prompt to maintain.

It evaluates only the six supported attributes: `category`, `size`, `price`,
`usage`, `color`, and `brand`. Extracted attributes are scored by exact
attribute/value pairs after presentation normalization. Missing metadata is
scored by the full `(attribute, priority, status)` record, so a field with the
wrong priority or a status other than `"missing"` is not counted as correct.

The script reports micro precision, recall, F1, and exact-set accuracy for
missing metadata. It reads model responses from `judge_runner/results/` and
writes detailed reports to `judge_runner/logs/`.
