"""Regression tests for the smart-chunking + merge + token-observability code.

These are pure unit tests (no live server) so they run in any CI quickly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the backend package is importable when pytest is run from /app or /app/backend.
_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))

from services.extraction_pipeline import (  # noqa: E402
    ChunkingService,
    _estimate_tokens,
    _merge_chunked_structured,
)


# ---------------------------------------------------------------------------
# ChunkingService
# ---------------------------------------------------------------------------
class TestChunkingService:
    def test_short_doc_returns_single_chunk(self) -> None:
        text = "# Doc\n\nHello world."
        assert ChunkingService().chunk(text) == [text]

    def test_long_doc_with_headings_produces_multiple_chunks(self) -> None:
        long_doc = "\n\n".join(
            f"## Section {i}\n\n" + ("x" * 4500) for i in range(5)
        )
        chunks = ChunkingService().chunk(long_doc)
        assert len(chunks) >= 2
        # Every chunk should contain at least one heading so the LLM keeps context.
        for chunk in chunks:
            assert "## Section" in chunk

    def test_chunks_respect_chunk_size_limit_within_overlap(self) -> None:
        # With chunk_size=8000 + chunk_overlap=400, no chunk should grossly exceed
        # the configured budget (some slack for the overlap tail and section glue).
        long_doc = "\n\n".join(f"## S{i}\n\n" + ("x" * 4500) for i in range(5))
        chunks = ChunkingService().chunk(long_doc)
        for chunk in chunks:
            assert len(chunk) <= 8000 + 800, f"chunk too large: {len(chunk)}"


# ---------------------------------------------------------------------------
# _merge_chunked_structured
# ---------------------------------------------------------------------------
class TestMerge:
    def test_single_partial_returns_itself(self) -> None:
        partials = [{"a": 1, "b": [1, 2]}]
        assert _merge_chunked_structured(partials) == {"a": 1, "b": [1, 2]}

    def test_lists_are_concatenated(self) -> None:
        partials = [{"lineItems": [{"d": "A"}]}, {"lineItems": [{"d": "B"}]}]
        merged = _merge_chunked_structured(partials)
        assert merged == {"lineItems": [{"d": "A"}, {"d": "B"}]}

    def test_dicts_deep_merge_without_clobbering(self) -> None:
        partials = [
            {"header": {"invoiceNumber": "INV-1", "date": "2026-01-01"}},
            {"header": {"customer": "Acme"}},
        ]
        merged = _merge_chunked_structured(partials)
        assert merged["header"] == {
            "invoiceNumber": "INV-1",
            "date": "2026-01-01",
            "customer": "Acme",
        }

    def test_scalar_first_non_empty_wins(self) -> None:
        partials = [{"invoiceNumber": "INV-1"}, {"invoiceNumber": "INV-LATER"}]
        merged = _merge_chunked_structured(partials)
        assert merged["invoiceNumber"] == "INV-1"

    def test_non_dict_partials_fall_back_to_documentChunks(self) -> None:
        partials = ["a string", {"x": 1}]
        merged = _merge_chunked_structured(partials)
        assert merged == {"documentChunks": ["a string", {"x": 1}]}


# ---------------------------------------------------------------------------
# _estimate_tokens
# ---------------------------------------------------------------------------
class TestEstimateTokens:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("", 0),
            ("abcd", 1),       # 4 chars / 4 -> 1
            ("a" * 7, 2),      # ceil(7 / 4) = 2
            ("a" * 12000, 3000),
        ],
    )
    def test_char_per_4_estimator(self, text: str, expected: int) -> None:
        assert _estimate_tokens(text) == expected
