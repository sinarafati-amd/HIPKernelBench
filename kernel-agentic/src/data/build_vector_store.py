import pdfplumber, faiss, yaml, pickle, argparse, os, textwrap
from .embeddings import get_embedder
import numpy as np
def chunk(txt, n):
    for i in range(0, len(txt), n):
        yield txt[i:i+n]

def main(pdf):
    cfg = yaml.safe_load(open("config.yml"))["rag"]
    embed = get_embedder()
    paragraphs, ids = [], []
    with pdfplumber.open(pdf) as pdf_in:
        for p in pdf_in.pages:
            paragraphs.extend(chunk(p.extract_text(), cfg["chunk_size"]))
    raw_vecs = embed.embed_documents(paragraphs)
    vecs = np.array(raw_vecs, dtype="float32")
    assert vecs.ndim == 2, f"Expected 2D array, got shape {vecs.shape}"
    index = faiss.IndexFlatL2(vecs.shape[1])
    index.add(vecs)
    os.makedirs("vector_store", exist_ok=True)
    faiss.write_index(index, "vector_store/store.index")
    with open("vector_store/text.pkl", "wb") as f:
        pickle.dump(paragraphs, f)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    main(ap.parse_args().pdf)
