#!/usr/bin/env python3
import os
import sys
import pickle

# 1) Ensure project root is on PYTHONPATH
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

import faiss
import numpy as np
from src.data.embeddings import get_embedder

def main():
    # --- load text chunks and index ---
    with open(os.path.join(ROOT, "vector_store", "text.pkl"), "rb") as f:
        texts = pickle.load(f)
    index = faiss.read_index(os.path.join(ROOT, "vector_store", "store.index"))

    # --- embed the query and build a (1×D) float32 array ---
    query = "How to launch HIP kernel"
    vec_list = get_embedder().embed_query(query)
    vec = np.array(vec_list, dtype="float32").reshape(1, -1)

    # --- search top-5 ---
    D, I = index.search(vec, 5)

    print("Distances:\n", D)
    print("Indices:\n", I)
    print("\nTop hits:")
    for idx in I[0]:
        print(f"- {texts[idx]!r}")

if __name__ == "__main__":
    main()
