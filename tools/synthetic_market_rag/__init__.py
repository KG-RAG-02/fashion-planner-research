"""Synthetic-only RAG fixtures and evaluation utilities.

This package is deliberately outside ``src/market_research_agent`` and is
never imported by the production workflow.
"""

__all__ = ["DEFAULT_DOCUMENT_COUNT", "MAX_DOCUMENT_COUNT", "generate_corpus", "evaluate_fixture", "lexical_retrieve"]


def __getattr__(name: str):
    if name in {"DEFAULT_DOCUMENT_COUNT", "MAX_DOCUMENT_COUNT", "generate_corpus"}:
        from . import generate
        return getattr(generate, name)
    if name in {"evaluate_fixture", "lexical_retrieve"}:
        from . import evaluate
        return getattr(evaluate, name)
    raise AttributeError(name)
