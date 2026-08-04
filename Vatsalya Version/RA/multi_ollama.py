"""Round-robin load balancer across multiple Ollama servers, for LightRAG's
entity/relation extraction calls (``llm_model_func``) and, via
``MultiOllamaLoadBalancer.embed``, chunk embedding too -- both share the
same ``servers.txt`` list and the same round-robin cycle/lock, so embedding
work lands evenly across whichever servers are configured rather than
pinning it to a single host. There is no separate "embedding server" -- the
whole point of round-robinning it too is that a single dedicated host is
either idle (wasted capacity) or a bottleneck (see ingest.py's EMBED_BACKEND
comment for why a laptop-side single-server embedding setup throttled the
whole pipeline).

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

from lightrag.llm.ollama import ollama_embed as _ollama_embed_wrapped
from lightrag.llm.ollama import ollama_model_complete

# lightrag.llm.ollama.ollama_embed is decorated at module level with
# @wrap_embedding_func_with_attrs(embedding_dim=1024, ..., model_name="bge-m3:latest")
# (its own bge-m3 defaults, unrelated to whatever EMBED_MODEL/EMBED_DIM this
# project actually uses) -- calling the decorated object directly runs
# EmbeddingFunc.__call__, which validates the returned vectors against ITS
# OWN embedding_dim=1024, not the 768 nomic-embed-text actually returns.
# That raised "Embedding dimension mismatch... expected dimension (1024)"
# for every call once embedding started actually reaching Ollama (round-
# robin embedding surfaced it; the earlier single dead-host setup never got
# far enough to hit this). ``.func`` is the raw, undecorated async function
# underneath -- calling that instead skips the wrapper's hardcoded-1024
# check entirely, leaving dimension validation to ingest.py's own
# EmbeddingFunc(embedding_dim=EMBED_DIM, ...) wrapper around this whole
# module, which uses the correct value.
ollama_embed = _ollama_embed_wrapped.func


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

    async def embed(self, texts, embed_model, **kwargs):
        """Load-balanced wrapper around ollama_embed, same round-robin cycle
        (and lock) as __call__ -- an embedding call and an extraction call
        each just take "whichever server is next", so the two workloads
        interleave across every configured server instead of embedding
        piling onto one host while extraction spreads across the rest.

        ``kwargs`` must NOT already contain "host", same constraint as
        __call__ (see its docstring)."""
        server = await self.get_next_server()
        return await ollama_embed(
            texts,
            embed_model=embed_model,
            host=server,
            **kwargs,
        )
