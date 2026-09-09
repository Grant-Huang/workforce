"""Memory tests: scoring parity with memory.js, and the AgentNexus round trip.

The AgentNexus half runs against `web-demo/agentnexus_mock.py` itself rather than a
second mock written for tests -- that module is the contract both arms talk to, so a
copy here could drift from it without anything failing.
"""
import agentnexus_mock
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from pipecat_demo.memory import AgentNexusMemory, LocalMemoryStore, tokenize


@pytest.fixture
async def agentnexus_url():
    """Serve the real AgentNexus mock and yield its base URL."""
    app = web.Application()
    agentnexus_mock.register(app)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    yield f"http://127.0.0.1:{server.port}/agentnexus-mock"
    await client.close()


def test_tokenize_indexes_individual_cjk_characters():
    """Chinese retrieval depends on this: no segmenter, so characters are the tokens."""
    tokens = tokenize("下午开会")
    assert {"下", "午", "开", "会"} <= tokens


def test_tokenize_lowercases_and_splits_punctuation():
    tokens = tokenize("Hello, World! 你好。")
    assert "hello" in tokens
    assert "world" in tokens
    assert "你" in tokens


def test_search_requires_overlap():
    store = LocalMemoryStore()
    store.add("下午三点跟智枢团队开会")
    assert store.search("完全无关的英文 query xyz") == []


def test_search_ranks_overlap_above_recency():
    """More matching tokens wins even when the other entry is newer."""
    store = LocalMemoryStore()
    old = store.add("下午三点开会，晚上七点健身")
    old.timestamp_ms -= 3 * 86_400_000  # three days old
    store.add("我喜欢喝咖啡")

    hits = store.search("下午开会")
    assert hits[0].text.startswith("下午三点开会")


def test_add_ignores_trivial_text():
    store = LocalMemoryStore()
    assert store.add(" ") is None
    assert store.add("好") is None
    assert store.add("好的") is not None


def test_merge_replaces_remote_subset_but_keeps_local():
    """Same reconciliation rule as memory.js: local entries are a separate lifecycle."""
    from pipecat_demo.memory import MemoryEntry

    store = LocalMemoryStore()
    store.add("本机产生的一条记忆")
    store.merge([MemoryEntry(text="远端旧记忆", timestamp_ms=1000, source="agentnexus", source_id="a")])
    assert len(store.all()) == 2

    store.merge([MemoryEntry(text="远端新记忆", timestamp_ms=2000, source="agentnexus", source_id="b")])
    texts = [e.text for e in store.all()]
    assert "远端旧记忆" not in texts
    assert "远端新记忆" in texts
    assert "本机产生的一条记忆" in texts


def test_merge_keeps_newer_local_copy():
    from pipecat_demo.memory import MemoryEntry

    store = LocalMemoryStore()
    store.merge([MemoryEntry(text="第一版", timestamp_ms=5000, source="agentnexus", source_id="a")])
    store.merge(
        [MemoryEntry(text="过期的重复拉取", timestamp_ms=1000, source="agentnexus", source_id="a")]
    )
    assert [e.text for e in store.all()] == ["第一版"]


async def test_pull_loads_seed_entries_and_makes_them_searchable(agentnexus_url):
    memory = AgentNexusMemory(
        base_url=agentnexus_url, channel_id="demo-channel", token="pt_mock_demo_token"
    )
    try:
        count = await memory.pull()
        assert count == 3

        hits = memory.search("下午开会")
        assert hits, "seeded schedule entry should be retrievable"
        assert any("开会" in hit for hit in hits)
    finally:
        await memory.close()


async def test_pull_failure_degrades_quietly():
    """A missing memory backend must not break the conversation."""
    memory = AgentNexusMemory(
        base_url="http://127.0.0.1:1/agentnexus-mock",
        channel_id="demo-channel",
        token="pt_mock_demo_token",
    )
    try:
        assert await memory.pull() == 0
        assert memory.search("下午开会") == []
    finally:
        await memory.close()


async def test_push_message_records_both_sides(agentnexus_url):
    memory = AgentNexusMemory(
        base_url=agentnexus_url, channel_id="demo-channel", token="pt_mock_demo_token"
    )
    try:
        await memory.push_message("用户说的话", "user")
        await memory.push_message("助手的回复", "assistant")

        session = await memory._get_session()
        async with session.get(
            f"{agentnexus_url}/api/v1/channels/demo-channel/messages",
            headers={"Authorization": "Bearer pt_mock_demo_token"},
        ) as resp:
            messages = await resp.json()

        assert [m["sender_type"] for m in messages] == ["user", "assistant"]
        assert [m["content"] for m in messages] == ["用户说的话", "助手的回复"]
    finally:
        await memory.close()
