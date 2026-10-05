import pytest

from support_agent.rag.chunking import chunk_markdown

DOC = (
    """# Returns

Intro text.

## Window
Ten days.

## Refunds
"""
    + ("Long paragraph. " * 40 + "\n\n") * 4
)


def test_splits_on_headings_and_keeps_titles() -> None:
    chunks = chunk_markdown(DOC, source="returns.md", max_chars=400, overlap_chars=50)
    titles = [c.title for c in chunks]
    assert titles[0] == "Returns"
    assert "Window" in titles
    assert titles.count("Refunds") > 1  # long section was split
    assert all(len(c.content) <= 400 for c in chunks)


def test_ids_are_stable_across_runs() -> None:
    a = chunk_markdown(DOC, source="returns.md")
    b = chunk_markdown(DOC, source="returns.md")
    assert [c.id for c in a] == [c.id for c in b]
    assert [c.content_hash for c in a] == [c.content_hash for c in b]


def test_rejects_bad_overlap() -> None:
    with pytest.raises(ValueError, match="overlap"):
        chunk_markdown(DOC, source="x.md", max_chars=100, overlap_chars=100)
