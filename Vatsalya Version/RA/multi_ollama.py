"""Multi-Ollama server load balancer for LightRAG extraction."""

import os
import asyncio
from itertools import cycle
from lightrag.llm.ollama import ollama_model_complete


def load_servers(servers_file="servers.txt"):
    """Load server URLs from servers.txt (one URL per line, ignore comments)."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    servers_path = os.path.join(base_dir, servers_file)

    servers = []
    if os.path.exists(servers_path):
        with open(servers_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    servers.append(line)

    if not servers:
        raise ValueError(f"No servers found in {servers_path}")

    print(f"Loaded {len(servers)} Ollama servers: {servers}")
    return servers


class MultiOllamaLoadBalancer:
    """Round-robin load balancer across multiple Ollama servers."""

    def __init__(self, servers):
        self.servers = servers
        self.server_cycle = cycle(servers)
        self.request_count = 0

    def get_next_server(self):
        """Get next server in round-robin order."""
        server = next(self.server_cycle)
        self.request_count += 1
        return server

    async def __call__(self, prompt, system_prompt=None, **kwargs):
        """Load-balanced wrapper around ollama_model_complete.

        Matches ollama_model_complete signature:
        (prompt, system_prompt=None, history_messages=[], **kwargs)
        """
        server = self.get_next_server()

        # Call ollama_model_complete with the selected server
        result = await ollama_model_complete(
            prompt,
            system_prompt=system_prompt,
            host=server,
            **kwargs
        )

        return result
