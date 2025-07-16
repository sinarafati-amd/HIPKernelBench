from __future__ import annotations
import faiss, pickle, yaml, os
from .base_agent import BaseAgent
from ..data.embeddings import get_embedder
from typing import List
import numpy as np

_CFG  = yaml.safe_load(open("config.yml"))
_RAG  = _CFG["rag"]                # chunk / k / lambda …
_PIPE = _CFG["Pipeline"]
_DFLT_MODE = _CFG.get("default_mode", "md")  # "md" | "pdf"
_TOP_K = _RAG.get("top_k", 3) 
_MIN_SIM = _RAG.get("min_similarity", 0.40)  # cosine floor [‑1 … 1]

# ---------------------------------------------------------------------------
# Helper – Maximal Marginal Relevance (cosine space)
# ---------------------------------------------------------------------------
def _mmr(query: np.ndarray, docs: np.ndarray, k: int, λ: float) -> List[int]:
    """Return *indices* of `k` docs chosen via λ‑MMR (cosine similarity)."""
    # normalise
    query = query / (np.linalg.norm(query) + 1e-9)
    docs_n = docs / (np.linalg.norm(docs, axis=1, keepdims=True) + 1e-9)

    sim_q = docs_n @ query  # (n,)
    selected: List[int] = []
    candidates: List[int] = list(range(len(docs)))

    while candidates and len(selected) < k:
        if not selected:
            best_local = int(np.argmax(sim_q[candidates]))
            selected.append(candidates.pop(best_local))
            continue

        mmr_scores = []
        for c in candidates:
            diversity = max(docs_n[c] @ docs_n[s] for s in selected)
            score = λ * sim_q[c] - (1 - λ) * diversity
            mmr_scores.append(score)
        best_local = int(np.argmax(mmr_scores))
        selected.append(candidates.pop(best_local))

    return selected


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------
class RAGResearcher(BaseAgent):
    """Retrieve top documentation chunks relevant to a PyTorch code explanation."""

    def __init__(self, kernel_lang: str | None = None, mode: str | None = None):
        super().__init__(
            "rag_researcher",
            system_prompt=(
                "You are an assistant that extracts ONLY the sentences or bullet‑points "
                "from provided documentation chunks that directly help the user translate "
                "a PyTorch snippet to another language."
            ),
        )

        self.lang = (kernel_lang or _PIPE.get("kernel_lang", "hip")).lower()
        self.mode = (mode or _DFLT_MODE).lower()

        self.embed = get_embedder()

        # ---------- load FAISS store ----------
        base = f"vector_store/{self.lang}/{self.mode}"
        idx_f = os.path.join(base, "store.index")
        txt_f = os.path.join(base, "text.pkl")

        # Legacy single‑level path fallback
        if not (os.path.exists(idx_f) and os.path.exists(txt_f)):
            legacy = f"vector_store/{self.lang}"
            idx_f2, txt_f2 = os.path.join(legacy, "store.index"), os.path.join(legacy, "text.pkl")
            if os.path.exists(idx_f2) and os.path.exists(txt_f2):
                idx_f, txt_f = idx_f2, txt_f2
            else:
                raise FileNotFoundError(
                    f"Vector store not found for {self.lang.upper()} ({self.mode}). "
                    f"Run build_vector_store.py first."
                )

        self.index: faiss.Index = faiss.read_index(idx_f)
        self.texts: List[str] = pickle.load(open(txt_f, "rb"))
        print(
            f"Loaded {self.lang.upper()}‑{self.mode} store with {len(self.texts)} chunks"
        )

        # optional: pre‑compute all vectors (memory trade‑off)
        self._doc_vecs = self.index.reconstruct_n(0, self.index.ntotal)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def query(self, explanation: str) -> str:
        """Return up to three MD chunks or ``""`` if nothing clears similarity floor."""
        # ↳ Embed user explanation
        q = np.asarray(self.embed.embed_query(explanation), dtype="float32")
        q /= np.linalg.norm(q) + 1e-9
        # ↳ initial recall
        D, I = self.index.search(q.reshape(1, -1), _RAG["n_neighbors"])
        sims = D[0]
        idxs = I[0]

        # Filter by similarity threshold
        # apply threshold using the dynamic threshold variable
        keep = [(int(i), float(s)) for i, s in zip(idxs, sims) if float(s) >= 0.4]    
        if not keep:
            return ""

        cand_idx, cand_sim = zip(*keep)
        cand_vecs = self._doc_vecs[list(cand_idx)]
        cand_txts = [self.texts[i] for i in cand_idx]

        # diversity with MMR
        λ = _RAG["mmr_lambda"]
        sel_local = _mmr(q, cand_vecs, k=_TOP_K, λ=λ)
        hits = [cand_txts[i] for i in sel_local]

        return "\n---\n".join(hits)

