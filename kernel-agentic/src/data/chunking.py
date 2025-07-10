"""chunking.py – hierarchical semantic splitter
================================================
Keeps tables, code‑fences and image descriptions **intact** while producing token‑bounded
chunks.  The `hard_max_tokens` is treated as a **soft** limit—atomic blocks may exceed it
rather than being broken.
"""
from __future__ import annotations

import itertools, re, yaml, nltk, tiktoken
from typing import List, Iterable

CFG = yaml.safe_load(open("config.yml"))
MODEL_NAME = CFG["openai"].get("embedding_model", "text-embedding-3-small")

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def get_encoder(name: str = MODEL_NAME):
    try:
        return tiktoken.encoding_for_model(name)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")

_enc  = get_encoder()
_SENT = nltk.data.load("tokenizers/punkt/english.pickle")


def _tokens(text: str) -> int:
    """Number of BPE tokens for `text`."""
    return len(_enc.encode(text))

# -----------------------------------------------------------------------------
# Atomic‑block detection regexes
# -----------------------------------------------------------------------------
_CODE_FENCE_OPEN = re.compile(r"```.*")
_HEADING        = re.compile(r"^#{1,6}\s", re.M)
_IMG_LINE       = re.compile(r"^!\[.*?\]\(.*?\)$", re.M)
_TABLE_LINE     = re.compile(r"^\|.*\|$")


# -----------------------------------------------------------------------------
# Pass 1: split into **atomic blocks** (no further splitting allowed inside)
# -----------------------------------------------------------------------------

def _iter_atomic_blocks(text: str) -> Iterable[str]:
    """Yield atomic markdown blocks: headings, code fences, tables, images, or prose."""
    lines = text.splitlines()
    i, N = 0, len(lines)
    while i < N:
        line = lines[i]
        # --- code fence ------------------------------------------------------
        if _CODE_FENCE_OPEN.match(line):
            j = i + 1
            while j < N and not lines[j].startswith("```"):
                j += 1
            j = min(j + 1, N)  # include closing ``` if present
            yield "\n".join(lines[i:j])
            i = j
            continue

        # --- table -----------------------------------------------------------
        if _TABLE_LINE.match(line):
            j = i + 1
            while j < N and _TABLE_LINE.match(lines[j]):
                j += 1
            yield "\n".join(lines[i:j])
            i = j
            continue

        # --- image -----------------------------------------------------------
        if _IMG_LINE.match(line):
            yield line
            i += 1
            continue

        # --- heading ---------------------------------------------------------
        if _HEADING.match(line):
            yield line
            i += 1
            continue

        # --- prose paragraph -------------------------------------------------
        buf = [line]
        i += 1
        while i < N and lines[i].strip() and not any(
            r.match(lines[i]) for r in (_CODE_FENCE_OPEN, _TABLE_LINE, _IMG_LINE, _HEADING)
        ):
            buf.append(lines[i])
            i += 1
        # skip single blank line after paragraph (keeps paragraphs separate)
        while i < N and not lines[i].strip():
            i += 1
        yield "\n".join(buf)


# -----------------------------------------------------------------------------
# Pass 2: merge atomic blocks into ≤chunk_size chunks, but **never** split an
#         atomic block itself.  The size threshold is soft—atomic blocks can be
#         larger than `chunk_size` and will live alone in their chunk.
# -----------------------------------------------------------------------------

def semantic_split(text: str, chunk_size: int, hard_max: int | None = None) -> List[str]:
    """Return a list of semantically‑coherent chunks.

    Parameters
    ----------
    chunk_size: int
        Target token size for greedy packing.
    hard_max: int | None
        Absolute "must‑not‑exceed" size.  If None, no hard cap.
    """
    if hard_max is None:
        hard_max = chunk_size * 2  # allow overflow up to ×2 before forced split

    chunks: List[str] = []
    cur: List[str] = []

    def cur_tokens(extra: str = "") -> int:
        return _tokens("\n".join(cur + ([extra] if extra else [])))

    for block in _iter_atomic_blocks(text):
        blk_tokens = _tokens(block)
        # If adding block would push us over soft limit & we already have content → flush.
        if cur and cur_tokens(block) > chunk_size:
            chunks.append("\n".join(cur).strip())
            cur = []
        # If atomic block itself is huge, place it in its own chunk.
        if blk_tokens >= hard_max:
            if cur:
                chunks.append("\n".join(cur).strip())
                cur = []
            chunks.append(block.strip())
            continue
        cur.append(block)

    if cur:
        chunks.append("\n".join(cur).strip())

    # Pass 3: couple “Following X description” lines with the next chunk.
    final_chunks: List[str] = []
    desc_pat = re.compile(r"\*\*Following (table|image|equation)", re.I)
    i = 0
    while i < len(chunks):
        if i + 1 < len(chunks) and desc_pat.search(chunks[i]):
            final_chunks.append(chunks[i] + "\n" + chunks[i + 1])
            i += 2
        else:
            final_chunks.append(chunks[i])
            i += 1

    return [c for c in final_chunks if c.strip()]
