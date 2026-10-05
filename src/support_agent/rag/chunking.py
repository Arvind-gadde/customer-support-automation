"""Heading-aware markdown chunking.

Splitting on headings keeps each chunk about ONE topic, which matters more for
retrieval quality than the exact chunk size. Oversized sections are then split
on paragraph boundaries with a small overlap.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Chunk:
    id: str
    source: str
    title: str
    chunk_index: int
    content: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(f"{self.title}\n{self.content}".encode()).hexdigest()


def _sections(markdown: str, default_title: str) -> list[tuple[str, str]]:
    matches = list(_HEADING.finditer(markdown))
    if not matches:
        return [(default_title, markdown.strip())]

    sections: list[tuple[str, str]] = []
    preamble = markdown[: matches[0].start()].strip()
    if preamble:
        sections.append((default_title, preamble))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[match.end() : end].strip()
        if body:
            sections.append((match.group(2), body))
    return sections


def _split_long(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    pieces: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip()
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            pieces.append(current)
            current = f"{current[-overlap_chars:]}\n\n{paragraph}".strip()
        else:
            current = paragraph
        while len(current) > max_chars:  # a single giant paragraph
            pieces.append(current[:max_chars])
            current = current[max_chars - overlap_chars :]
    if current:
        pieces.append(current)
    return pieces


def chunk_markdown(
    markdown: str, *, source: str, max_chars: int = 1200, overlap_chars: int = 150
) -> list[Chunk]:
    if overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be smaller than max_chars")

    default_title = source.rsplit("/", 1)[-1].removesuffix(".md").replace("-", " ").title()
    chunks: list[Chunk] = []
    for title, body in _sections(markdown, default_title):
        for piece in _split_long(body, max_chars, overlap_chars):
            index = len(chunks)
            chunk_id = hashlib.sha256(f"{source}:{index}".encode()).hexdigest()[:32]
            chunks.append(Chunk(chunk_id, source, title, index, piece))
    return chunks
