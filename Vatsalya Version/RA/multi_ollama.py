"""Round-robin load balancer across multiple Ollama servers, for LightRAG's
entity/relation extraction calls (``llm_model_func``) only -- embedding
stays on the single ``OLLAMA_HOST`` in ``config.py``/``ingest.py``, since
extraction is the expensive, GPU-bound part this is meant to parallelize
across more than one server's worth of VRAM.

Each server in ``servers.txt`` is expected to be its own ``ollama serve``
process (typically pinned to a distinct GPU or GPU pair via
``CUDA_VISIBLE_DEVICES``, with its own ``OLLAMA_NUM_PARALLEL``) -- this
module doesn't start or manage those processes, only distributes requests
across whichever ones are listed.

**Concurrency note:** The round-robin cycle is guarded by an asyncio.Lock,
so concurrent async calls to get_next_server() serialize atomically and
each get a distinct server in order. This ensures that if LLM_MAX_ASYNC=4
and servers=[A,B,C,D], then up to 4 concurrent extraction requests will
land on all 4 servers (one each), not all on the same server.
"""

import asyncio
import os
from itertools import cycle

from lightrag.llm.ollama import ollama_model_complete


def load_servers(servers_file="servers.txt"):
    """Load server URLs from servers_file (one URL per line, '#' comments
    and blank lines ignored). A relative path (the production default,
    "servers.txt") is resolved against this module's own directory, not
    the working directory ingest.py happens to be launched from; an
    absolute path is used as-is (mainly so tests can point at a tmp_path
    fixture without needing to touch the real servers.txt).

    Hard-fails (no silent single-server fallback) if the file is missing
    or empty -- matching this folder's existing discipline (see
    IMPLEMENTATION.md #5): a missing servers.txt means the multi-server
    setup step (README.md) hasn't been done yet, not "just use OLLAMA_HOST
    and move on silently"."""
    if os.path.isabs(servers_file):
        servers_path = servers_file
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        servers_path = os.path.join(base_dir, servers_file)

    if not os.path.exists(servers_path):
        raise FileNotFoundError(
            f"{servers_path} does not exist. Create it with one Ollama server "
            "URL per line (see README.md's multi-server setup section) before "
            "running ingest.py."
        )

    servers = []
    with open(servers_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                servers.append(line)

    if not servers:
        raise ValueError(
            f"{servers_path} exists but has no server URLs (only comments/blank "
            "lines) -- add at least one Ollama server URL."
        )

    print(f"Loaded {len(servers)} Ollama server(s) for extraction: {servers}")
    return servers


class MultiOllamaLoadBalancer:
    """Round-robin dispatcher matching LightRAG's llm_model_func contract:
    ``async def (prompt, system_prompt=None, history_messages=[], **kwargs)``.

    Instances are stateful (server_cycle, request_count, _lock) -- construct
    one per build_rag() call, don't share across independent runs. The
    round-robin cycle is protected by an asyncio.Lock so concurrent calls
    serialize atomically and get distinct servers in order, ensuring
    parallelization across all configured servers."""

    def __init__(self, servers):
        if not servers:
            raise ValueError("MultiOllamaLoadBalancer requires at least one server")
        self.servers = servers
        self.server_cycle = cycle(servers)
        self.request_count = 0
        self._lock = asyncio.Lock()

    async def get_next_server(self):
        """Return the next server in round-robin order (thread-safe for concurrent calls)."""
        async with self._lock:
            server = next(self.server_cycle)
            self.request_count += 1
            return server

    async def __call__(self, prompt, system_prompt=None, **kwargs):
        """Load-balanced wrapper around ollama_model_complete.

        ``kwargs`` must NOT already contain "host" -- this call sets it
        per-request from the round-robin pick, so llm_model_kwargs passed
        to LightRAG(...) should carry only "options"/"think", not "host"
        (see ingest.py's llm_kwargs()). Passing both would raise
        "got multiple values for keyword argument 'host'"."""
        server = await self.get_next_server()
        return await ollama_model_complete(
            prompt,
            system_prompt=system_prompt,
            host=server,
            **kwargs,
        )
