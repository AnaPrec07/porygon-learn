from __future__ import annotations

import re

_SENT = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text.strip()) if s.strip()]


def chunk_text(text: str, max_chars: int = 900) -> list[str]:
    """Greedy paragraph packing; over-long paragraphs are split on sentences."""
    pieces: list[str] = []
    for para in re.split(r"\n\s*\n", text.strip()):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            pieces.append(para)
        else:
            cur = ""
            for sent in split_sentences(para):
                if cur and len(cur) + len(sent) + 1 > max_chars:
                    pieces.append(cur)
                    cur = sent
                else:
                    cur = f"{cur} {sent}".strip()
            if cur:
                pieces.append(cur)
    chunks: list[str] = []
    cur = ""
    for p in pieces:
        if cur and len(cur) + len(p) + 2 > max_chars:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}".strip()
    if cur:
        chunks.append(cur)
    return chunks
