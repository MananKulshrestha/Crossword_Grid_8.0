# Gemma 4 26B gateway integration

The concrete provider adapter is `fkgrid.query_recovery.adapters.gemini`. It is
pinned to Google's hosted Gemma 4 26B A4B instruction-tuned model:
`gemma-4-26b-a4b-it`.

Set the credential outside the repository. The preferred project setting is
`FKGRID_GEMINI_API_KEY`; `GEMINI_API_KEY` is accepted as a compatibility
fallback for the official Gemini tooling. Never put the value in source,
`.env` committed files, prompts, events, logs, or command output.

With Pydantic installed, run the redacted live smoke test from the repository
root:

```text
$env:PYTHONPATH = "src"
$env:FKGRID_GEMINI_API_KEY = "<set outside Git and shell history>"
python scripts/smoke_gemma4.py
```

The adapter uses one HTTPS `generateContent` call, temperature 0, JSON MIME
output, minimal thinking, and no tools or retries. Gemma can return separate
thought and answer parts; only non-thought text is passed to the strict local
`RecoveryPlannerOutput` validator. Provider failures return sanitized status
codes and preserve the recovery workflow's baseline/no-safe behavior.

The smoke script has two modes. The default diagnostic mode allows up to five
seconds to prove the provider and workflow path. The measured hosted calls in
this environment were approximately 4.4–4.7 seconds. Set
`FKGRID_GEMMA_SMOKE_MODE=production` to exercise the binding 1,800 ms recovery
deadline; it correctly times out and returns a safe clarification/fallback
without retrying. Do not increase the production deadline merely to hide this
provider latency; use a faster approved endpoint or a separately approved
budget change after evaluation.

The API key, provider SDK objects, raw response, raw prompt, and model thought
content do not cross the `StructuredModelGateway` boundary.
