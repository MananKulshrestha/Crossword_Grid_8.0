# DeepInfra Gemma 4 26B gateway integration

The default provider adapter is
`fkgrid.query_recovery.adapters.deepinfra.DeepInfraGemmaGateway`. It is pinned
to DeepInfra's OpenAI-compatible Gemma model:
`google/gemma-4-26B-A4B-it`.

DeepInfra's documented chat endpoint is
`https://api.deepinfra.com/v1/openai/chat/completions`. The adapter sends a
system message containing the versioned recovery prompt, a user message
containing canonical recovery context JSON, `temperature: 0`, a bounded
`max_tokens` value, `stream: false`, and `response_format: {"type":
"json_object"}`. The provider's JSON mode guarantees JSON syntax only; strict
local Pydantic validation remains authoritative for action, ID, hash, scope,
and hard-filter safety.

Set the credential outside the repository. The preferred project setting is
`FKGRID_DEEPINFRA_API_KEY`; `DEEPINFRA_API_KEY` and the official
`DEEPINFRA_TOKEN` name are accepted as process-environment fallbacks. Never
put the value in source, `.env` committed files, prompts, events, logs, or
command output. `SecretStr` masks it in runtime representations.

With the bundled dependencies installed, run the redacted live smoke test
from the repository root:

```text
$env:PYTHONPATH = "src"
$env:FKGRID_DEEPINFRA_API_KEY = "<set outside Git and shell history>"
python scripts/smoke_gemma4.py
```

The diagnostic smoke mode allows up to five seconds to prove the provider and
workflow path. Set `FKGRID_GEMMA_SMOKE_MODE=production` to exercise the
binding 1,800 ms recovery deadline and safe fallback. A provider response that
is slow, unauthorized, rate-limited, unavailable, malformed, or empty is
converted to a sanitized gateway code; the workflow preserves the baseline
or returns its typed no-safe outcome without retrying.

The API key, raw provider response, raw prompt, provider SDK objects, and
model reasoning content do not cross the `StructuredModelGateway` boundary.
The old Google Gemini adapter remains available for compatibility, but it is
not selected by the default FastAPI composition.
