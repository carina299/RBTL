#!/usr/bin/env python3
"""MCP server exposing the memory system and the photo album as
agent-callable tools (Agentic RAG — the agent decides if/when/how many times
to search or write memory, or add to the album, instead of a channel plugin
forcing a lookup every turn).

Registered into Claude Code's MCP config (see ../.mcp.json) alongside the
existing `companion` channel plugin — this server is independent of it and
talks straight to Postgres (and the object storage backend for the album
tools), not through the relay's HTTP API.

Run directly (stdio transport, what Claude Code expects):
    python3 mcp_server.py
"""

import mimetypes
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from album import create_album_photo
from database import session_scope
from memory.remember import remember
from memory.retrieval_graph import run_retrieval
from repositories import DEFAULT_USER_ID, AlbumRepository

mcp = FastMCP("rbtl-memory")


@mcp.tool()
def memory_search(query: str, top_k: int = 6) -> list[dict]:
    """Search the user's long-term memory for facts or past events relevant
    to `query`. Use this when answering something that depends on what the
    user has told you before — preferences, ongoing plans, corrected facts,
    anything from an earlier conversation you wouldn't otherwise remember.
    You can call this more than once in a turn (e.g. rephrase and search
    again if the first results aren't relevant).
    """
    with session_scope() as session:
        return run_retrieval(session, DEFAULT_USER_ID, query, top_k=top_k)


@mcp.tool()
def memory_remember(content: str, importance: float = 0.5, memory_type: str = "semantic") -> dict:
    """Save something the user just said as a long-term memory right now,
    instead of waiting for the periodic background pass to pick it up. Use
    this when the user shares something clearly worth remembering long-term —
    a preference, a correction to something you had wrong, a stable fact
    about their life. `memory_type`: 'semantic' for a stable fact (default),
    'episodic' for a specific, dated event.
    """
    with session_scope() as session:
        return remember(session, DEFAULT_USER_ID, content, memory_type=memory_type, importance=importance)


@mcp.tool()
def album_upload_photo(image_path: str, caption: str = "", time_label: str = "", group_name: str = "") -> dict:
    """Save a local image file to the user's photo album, with an optional
    caption/date/group tag. `image_path` must be a path to an image already
    on disk (e.g. one downloaded from an inbound chat attachment) — this
    does not fetch URLs or accept raw bytes. `time_label` is a free-text
    date like "2026-06-25"; it's parsed best-effort to drive timeline sort
    order but always displayed back to the user verbatim.
    """
    path = Path(image_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {image_path}")
    data = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with session_scope() as session:
        entry = create_album_photo(
            session, DEFAULT_USER_ID, data, path.name, mime,
            caption=caption, time_label=time_label, group_name=group_name,
        )
        return {"id": entry.id}


@mcp.tool()
def album_list_photos(limit: int = 20) -> list[dict]:
    """List the user's most recent album photos (newest moment first) —
    caption/date/group/view-count, no image bytes. Use this to recall what's
    already in the album, or to answer questions about it, before adding
    something new.
    """
    with session_scope() as session:
        entries = AlbumRepository(session).list_timeline(DEFAULT_USER_ID, cursor=None, limit=limit)
        return [
            {
                "id": e.id,
                "caption": e.caption,
                "time_label": e.time_label,
                "group_name": e.group_name,
                "views": e.views,
            }
            for e in entries
        ]


if __name__ == "__main__":
    mcp.run()
