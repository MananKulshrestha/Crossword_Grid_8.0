# Legacy Google Gemini Gemma 4 26B gateway

The default FastAPI composition now uses the DeepInfra adapter documented in
[`deepinfra-gemma4-integration.md`](deepinfra-gemma4-integration.md). This
file is retained because `fkgrid.query_recovery.adapters.gemini` remains a
backward-compatible, separately selectable Google REST adapter for callers
that already depend on it. It is not selected by the default app and its
`FKGRID_GEMINI_API_KEY`/`GEMINI_API_KEY` settings are not used by the new
DeepInfra path.
