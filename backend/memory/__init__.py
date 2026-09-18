"""Long-term memory for the companion agent (Agentic RAG / LangGraph).

This package holds the *write* side: embeddings, LLM-based candidate
extraction, the dedup decision shared by both write paths, and the
LangGraph `extraction_graph` (background) / `remember()` (explicit,
`memory_remember`) pipelines themselves.

The *retrieval* side (`retrieval_graph`, the MCP server exposing
`memory_search`/`memory_remember` to the agent) is a separate, later piece of
work and doesn't live here yet.
"""
