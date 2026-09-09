"""Memory retrieval for the cascaded pipeline.

Two deliberate reuse decisions, so that a comparison between the two arms is about the
pipeline and not about one of them having better memory:

* The scoring is a port of `LocalMemory.search` in web-demo/static/memory.js (keyword
  overlap + recency, no embeddings) -- same algorithm, same weights, same tokenizer
  quirk of indexing individual CJK characters.
* The entries come from the same AgentNexus mock that web-demo/server.py already
  serves (`/agentnexus-mock/*`), pulled over HTTP. Nothing is duplicated or seeded
  here: point `AGENTNEXUS_BASE_URL` at a running web-demo and both arms read the same
  memory. If it isn't running, retrieval returns nothing and the conversation
  continues without background info rather than failing.

What is *not* ported: `localStorage` persistence. This process is per-session, so
entries added during a session live in memory and the AgentNexus pull is the source of
truth -- which matches the proposal's "AgentNexus is the truth, local is a disposable
cache" split (docs/agentnexus-memory-integration-proposal.md).
"""
import re
import time
import uuid
from dataclasses import dataclass, field

import aiohttp
from loguru import logger

# Same split as memory.js: whitespace plus ASCII and full-width punctuation.
_SPLIT_RE = re.compile(r"[\s,.!?;:，。！？；：]+")
_NON_ASCII_RE = re.compile(r"[^\x00-\x7f]")


def tokenize(text: str) -> set[str]:
    """Tokenize the same way memory.js does.

    Words are lowercased and split on punctuation; every non-ASCII character is *also*
    indexed on its own, which is what makes this work at all for Chinese (there is no
    word segmentation here, so single-character overlap is the signal).
    """
    tokens: set[str] = set()
    for word in _SPLIT_RE.split((text or "").lower()):
        if not word:
            continue
        tokens.add(word)
        for char in word:
            if _NON_ASCII_RE.match(char):
                tokens.add(char)
    return tokens


@dataclass
class MemoryEntry:
    """One retrievable piece of memory."""

    text: str
    timestamp_ms: float
    source: str = "local"
    source_id: str | None = None
    layer: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


class LocalMemoryStore:
    """In-process port of memory.js's LocalMemory (minus localStorage)."""

    def __init__(self) -> None:
        self._entries: list[MemoryEntry] = []

    def add(self, text: str, **meta) -> MemoryEntry | None:
        """Store a turn. Returns None for text too short to be worth keeping."""
        trimmed = (text or "").strip()
        if len(trimmed) < 2:
            return None
        entry = MemoryEntry(text=trimmed, timestamp_ms=time.time() * 1000, **meta)
        self._entries.append(entry)
        return entry

    def merge(self, remote_entries: list[MemoryEntry]) -> None:
        """Replace the agentnexus-sourced subset with a fresh pull.

        Same reconciliation rule as memory.js: keyed on `source_id`, a local copy is
        kept when it is at least as new as the pulled one (guards against a late
        response clobbering fresher data), and locally-produced entries are untouched.
        """
        existing = {
            e.source_id: e
            for e in self._entries
            if e.source == "agentnexus" and e.source_id is not None
        }
        reconciled = []
        for remote in remote_entries:
            current = existing.get(remote.source_id)
            if current and current.timestamp_ms >= remote.timestamp_ms:
                reconciled.append(current)
            else:
                reconciled.append(remote)
        self._entries = [e for e in self._entries if e.source != "agentnexus"] + reconciled

    def search(self, query: str, limit: int = 5) -> list[MemoryEntry]:
        """Score by keyword overlap plus a recency bonus, exactly as memory.js does."""
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        now_ms = time.time() * 1000
        scored: list[tuple[float, MemoryEntry]] = []
        for entry in self._entries:
            overlap = len(query_tokens & tokenize(entry.text))
            if overlap == 0:
                continue
            age_days = max((now_ms - entry.timestamp_ms) / 86_400_000, 0)
            scored.append((overlap + 1 / (1 + age_days), entry))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in scored[:limit]]

    def all(self) -> list[MemoryEntry]:
        """Every entry, newest first."""
        return sorted(self._entries, key=lambda e: e.timestamp_ms, reverse=True)


class AgentNexusMemory:
    """LocalMemoryStore plus the AgentNexus REST calls agentnexus.js makes."""

    def __init__(
        self,
        *,
        base_url: str,
        channel_id: str,
        token: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._channel_id = channel_id
        self._token = token
        self._session = session
        self._owns_session = session is None
        self.store = LocalMemoryStore()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """Close the HTTP session if this object created it."""
        if self._session and self._owns_session and not self._session.closed:
            await self._session.close()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    async def pull(self) -> int:
        """Pull channel memory into the local store. Returns how many entries arrived.

        Failure is logged and swallowed: a missing memory backend should degrade the
        answer quality, not take down the conversation.
        """
        url = f"{self._base_url}/api/v1/channels/{self._channel_id}/memory/"
        try:
            session = await self._get_session()
            async with session.get(url, headers=self._headers(), timeout=_timeout(5)) as resp:
                resp.raise_for_status()
                remote = await resp.json()
        except Exception as e:
            logger.warning(f"AgentNexus pull failed, continuing without it: {e}")
            return 0

        entries = []
        for item in remote:
            title = item.get("title")
            content = item.get("content") or ""
            text = f"{title}：{content}" if title else content
            entries.append(
                MemoryEntry(
                    text=text,
                    timestamp_ms=_parse_iso_ms(item.get("updated_at")),
                    source="agentnexus",
                    source_id=item.get("entry_id"),
                    layer=item.get("layer"),
                )
            )
        self.store.merge(entries)
        return len(entries)

    async def push_message(self, text: str, sender_type: str = "user") -> None:
        """Record a raw turn as a channel message (both sides, like history.js does)."""
        url = f"{self._base_url}/api/v1/channels/{self._channel_id}/messages"
        payload = {"content": text, "sender_type": sender_type}
        try:
            session = await self._get_session()
            async with session.post(
                url, headers=self._headers(), json=payload, timeout=_timeout(5)
            ) as resp:
                resp.raise_for_status()
        except Exception as e:
            logger.warning(f"AgentNexus message push failed ({sender_type}): {e}")

    def search(self, query: str, limit: int = 5) -> list[str]:
        """Retrieve relevant memory text for a user utterance."""
        return [entry.text for entry in self.store.search(query, limit)]


def _timeout(seconds: float) -> aiohttp.ClientTimeout:
    return aiohttp.ClientTimeout(total=seconds)


def _parse_iso_ms(value: str | None) -> float:
    """Parse AgentNexus's ISO timestamps; fall back to "now" like agentnexus.js does."""
    if not value:
        return time.time() * 1000
    try:
        from datetime import datetime

        return datetime.fromisoformat(value).timestamp() * 1000
    except ValueError:
        return time.time() * 1000
