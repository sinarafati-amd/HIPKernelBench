import faiss, pickle, yaml, os
from .base_agent import BaseAgent
from ..data.embeddings import get_embedder
import numpy as np

CFG = yaml.safe_load(open("config.yml"))["rag"]
PIPELINE_CFG = yaml.safe_load(open("config.yml"))["Pipeline"]

class RAGResearcher(BaseAgent):
    def __init__(self, kernel_lang: str = None):
        super().__init__("rag_researcher",
            """You are an assistant that extracts ONLY the sentences or bullet-points from provided documentation chunks that directly answer the user question.""")
        
        if kernel_lang is None:
            kernel_lang = PIPELINE_CFG.get("kernel_lang", "hip").lower()
        
        self.kernel_lang = kernel_lang.lower()
        self.embed = get_embedder()
        
        # Load language-specific vector store
        store_path = f"vector_store/{self.kernel_lang}"
        index_file = os.path.join(store_path, "store.index")
        text_file = os.path.join(store_path, "text.pkl")
        
        if not os.path.exists(index_file) or not os.path.exists(text_file):
            print(f"Warning: Vector store for {self.kernel_lang} not found at {store_path}")
            print(f"Please run: python -m src.data.build_vector_store --language {self.kernel_lang}")
            # Create empty fallbacks
            self.index = None
            self.texts = []
        else:
            try:
                self.index = faiss.read_index(index_file)
                self.texts = pickle.load(open(text_file, "rb"))
                print(f"Loaded {self.kernel_lang.upper()} vector store with {len(self.texts)} chunks")
            except Exception as e:
                print(f"Error loading vector store for {self.kernel_lang}: {e}")
                self.index = None
                self.texts = []

    def query(self, question: str) -> str:
        if self.index is None or not self.texts:
            return f"# No {self.kernel_lang.upper()} documentation available\n# Please build vector store first"
        
        try:
            qvec = self.embed.embed_query(question)
            vec  = np.array(qvec, dtype="float32").reshape(1, -1)
            D, I = self.index.search(vec, CFG["n_neighbors"])
            
            hits = [
                self.texts[idx]
                for dist, idx in zip(D[0], I[0])
                if dist < CFG["similarity_threshold"] and idx < len(self.texts)
            ]
            
            if not hits:
                return f"# No relevant {self.kernel_lang.upper()} documentation"
            
            return "\n".join(hits)
            
        except Exception as e:
            print(f"Error querying {self.kernel_lang} vector store: {e}")
            return f"# Error accessing {self.kernel_lang.upper()} documentation"
