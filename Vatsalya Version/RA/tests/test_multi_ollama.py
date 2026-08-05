"""Tests for multi_ollama's server-list parsing and round-robin dispatch.

No live Ollama server needed here -- load_servers() is pure file parsing,
and MultiOllamaLoadBalancer.get_next_server() is pure round-robin logic,
independent of the actual network call in __call__. That call itself is
exercised for real once ingest.py runs against live Ollama, not here (this
folder's existing tests -- test_sql_filter.py, test_bm25.py,
test_graph_sampling.py -- verify real components against real data, not
mocks; multi_ollama.py's own network leg has nothing to verify without a
live server, so it's exempted the same way IMPLEMENTATION.md's "no soft
fallbacks" discipline exempts genuinely untestable-without-infra pieces).
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from multi_ollama import load_servers, MultiOllamaLoadBalancer  # noqa: E402


def test_load_servers_reads_committed_servers_txt():
    servers = load_servers("servers.txt")
    assert servers == ["http://127.0.0.1:11437", "http://127.0.0.1:11438"]


def test_load_servers_ignores_comments_and_blank_lines(tmp_path):
    servers_file = tmp_path / "servers.txt"
    servers_file.write_text(
        "# comment\n"
        "\n"
        "http://localhost:11435\n"
        "  \n"
        "http://localhost:11436  \n"
        "# http://localhost:11437 (disabled)\n"
    )
    servers = load_servers(str(servers_file))
    assert servers == ["http://localhost:11435", "http://localhost:11436"]


def test_load_servers_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_servers(str(tmp_path / "does_not_exist.txt"))


def test_load_servers_empty_file_raises(tmp_path):
    empty_file = tmp_path / "empty.txt"
    empty_file.write_text("# only a comment\n\n")
    with pytest.raises(ValueError):
        load_servers(str(empty_file))


def test_round_robin_cycles_in_order():
    lb = MultiOllamaLoadBalancer(["a", "b", "c"])
    async def collect():
        return [await lb.get_next_server() for _ in range(7)]

    picks = asyncio.run(collect())
    assert picks == ["a", "b", "c", "a", "b", "c", "a"]


def test_round_robin_counts_requests():
    lb = MultiOllamaLoadBalancer(["a", "b"])
    async def collect():
        for _ in range(5):
            await lb.get_next_server()

    asyncio.run(collect())
    assert lb.request_count == 5


def test_single_server_always_returns_itself():
    lb = MultiOllamaLoadBalancer(["only-one"])
    async def collect():
        return [await lb.get_next_server() for _ in range(3)]

    assert asyncio.run(collect()) == ["only-one"] * 3


def test_empty_server_list_raises():
    with pytest.raises(ValueError):
        MultiOllamaLoadBalancer([])
