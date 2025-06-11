import faiss, pickle, yaml
from .base_agent import BaseAgent
from ..data.embeddings import get_embedder
import numpy as np
CFG = yaml.safe_load(open("config.yml"))["rag"]

class RAGResearcher(BaseAgent):
    def __init__(self):
        super().__init__("rag_researcher",
            """You are an assistant that extracts ONLY the sentences or bullet-points from provided documentation chunks that directly answer the user question.""")
        self.embed = get_embedder()
        self.index = faiss.read_index("vector_store/store.index")
        self.texts = pickle.load(open("vector_store/text.pkl", "rb"))

    def query(self, question: str) -> str:
        qvec = self.embed.embed_query(question)
        vec  = np.array(qvec, dtype="float32").reshape(1, -1)
        D, I = self.index.search(vec, CFG["n_neighbors"])
        hits = [
            self.texts[idx]
            for dist, idx in zip(D[0], I[0])
            if dist < CFG["similarity_threshold"]
        ]
        return "\n".join(hits)
