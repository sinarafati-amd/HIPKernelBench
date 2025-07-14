
from __future__ import annotations
import pdfplumber, faiss, yaml, pickle, argparse, os, textwrap, glob
import shutil, subprocess, sys, tempfile
from .embeddings import get_embedder
from .chunking import semantic_split
from typing import List
from pathlib import Path
import numpy as np

_CFG = yaml.safe_load(open("config.yml"))
_RAG = _CFG["rag"]


# ---------- helpers -------------------
# --------------------------------------
def _chunk_plain(text: str, n_tokens: int) -> List[str]:
    """Old simple fixed-size chunker (≈ characters)."""
    return [text[i : i + n_tokens] for i in range(0, len(text), n_tokens)]


def _collect_text(files: List[str], mode: str) -> List[str]:
    """
    Extract & chunk documents.
    In multimodal mode each *file* is already an enhanced MD file.
    """

    blocks: List[str] = []
    if mode == "text":
        for fp in files:
            if fp.endswith(".pdf"):
                with pdfplumber.open(fp) as pdf:
                    for page in pdf.pages:
                        blocks.append(page.extract_text() or "")
            else:
                blocks.append(Path(fp).read_text(encoding="utf-8", errors="ignore"))
        # simple chunking
        chunks: List[str] = []
        for txt in blocks:
            chunks.extend(_chunk_plain(txt, _RAG["chunk_size"]))
    else:  # multimodal
        for md in files:
            txt = Path(md).read_text(encoding="utf-8", errors="ignore")
            blocks.extend(semantic_split(txt, _RAG["chunk_size"]))
        chunks = blocks  # already split
    print(f"  ⮑ {len(chunks)} chunks ready")
    return chunks


def _write_store(chunks: List[str], store_dir: Path, embed) -> None:
    vecs = np.asarray(embed.embed_documents(chunks), dtype="float32")
    faiss.normalize_L2(vecs)
    index = faiss.IndexFlatIP(vecs.shape[1])
    index.add(vecs)

    store_dir.mkdir(parents=True, exist_ok=True)
    index_path = store_dir / "store.index"
    faiss.write_index(index, str(index_path))
    with (store_dir / "text.pkl").open("wb") as f:
        pickle.dump(chunks, f)
    print(f"  ✔ Saved vector store → {store_dir}")


# ---------- multimodal preprocessing ---------------------------------------
def _run_docling(pdf_path: Path, scratch: Path) -> List[Path]:
    """
    Convert one PDF to enhanced markdown(s) via pdf_digest.
    Returns list of *.md files created.
    """
    scratch.mkdir(exist_ok=True, parents=True)
    cmd = [
        sys.executable,
        os.path.join(Path(__file__).parent.parent,'utils','pdf_digest.py'),
        "--pdf_path",  str(pdf_path),
        "--output_dir", str(scratch),
    ]
    print(f"  → Docling digest: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    # pick the enhanced markdown(s)
    return list(scratch.glob("*-with-image-refs-enhanced.md"))


def _prepare_multimodal_docs(docs_path: Path) -> List[Path]:
    """
    For every PDF in docs_path, run Docling if enhanced MD not cached.
    Caches are stored next to the PDF (*/docname_mm/*).
    """
    md_files: List[Path] = []

    for pdf in docs_path.glob("*.pdf"):
        cache_dir = pdf.with_suffix("").with_name(pdf.stem + "_mm")
        enhanced = list(cache_dir.glob("*-with-image-refs-enhanced.md"))
        if not enhanced:
            # (re)generate
            if cache_dir.exists():
                shutil.rmtree(cache_dir)
            cache_dir.mkdir()
            enhanced = _run_docling(str(pdf.resolve()), cache_dir)
        md_files.extend(enhanced)
    return md_files


# ---------- build per language ---------------------------------------------
def build(language: str, docs_path: str | None, mode: str) -> None:
    embed = get_embedder()
    docs_dir = Path(docs_path) if docs_path else Path("docs") / language
    if not docs_dir.exists():
        print(f"⚠  Docs path missing: {docs_dir}")
        return

    if mode == "multimodal":
        files = _prepare_multimodal_docs(docs_dir)
    else:  # text
        pdfs = list(docs_dir.glob("*.pdf"))
        texts = [*docs_dir.rglob("*.txt")]
        files = pdfs + texts

    if not files:
        print(f"⚠  No source files found for {language.upper()} ({mode})")
        return

    print(f"Building store for {language.upper()} ({mode}) from {len(files)} file(s)")
    chunks = _collect_text([str(f) for f in files], mode)
    store_dir = Path("vector_store") / language 
    _write_store(chunks, store_dir, embed)


def _all_languages(args):
    for lang in ["hip", "cuda", "triton"]:
        build(lang, args.docs_path, args.mode)


# ---------- CLI ------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--language", choices=["hip", "cuda", "triton"])
    ap.add_argument("--docs-path", help="Custom docs folder")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--mode", choices=["text", "multimodal"],
                    default=_CFG.get("default_mode", "text"))
    args = ap.parse_args()

    if args.all:
        _all_languages(args)
    elif args.language:
        build(args.language, args.docs_path, args.mode)
    else:
        ap.error("Choose --language or --all")


if __name__ == "__main__":
    main()
