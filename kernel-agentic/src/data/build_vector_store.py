import pdfplumber, faiss, yaml, pickle, argparse, os, textwrap, glob
from .embeddings import get_embedder
import numpy as np

def chunk(txt, n):
    for i in range(0, len(txt), n):
        yield txt[i:i+n]

def build_language_store(language: str, docs_path: str = None):
    """Build vector store for a specific language"""
    cfg = yaml.safe_load(open("config.yml"))["rag"]
    embed = get_embedder()
    
    if docs_path is None:
        docs_path = f"docs/{language}"
    
    if not os.path.exists(docs_path):
        print(f"Warning: Documentation path {docs_path} does not exist for {language}")
        return
    
    # Find all PDF files in the language-specific docs folder
    pdf_files = glob.glob(os.path.join(docs_path, "*.pdf"))
    
    if not pdf_files:
        print(f"Warning: No PDF files found in {docs_path}")
        return
    
    print(f"Building vector store for {language} from {len(pdf_files)} PDF(s)...")
    
    paragraphs = []
    for pdf_file in pdf_files:
        print(f"  Processing: {pdf_file}")
        with pdfplumber.open(pdf_file) as pdf_in:
            for p in pdf_in.pages:
                text = p.extract_text()
                if text:  # Only process non-empty pages
                    paragraphs.extend(chunk(text, cfg["chunk_size"]))
    
    if not paragraphs:
        print(f"Warning: No text extracted from PDFs in {docs_path}")
        return
    
    print(f"  Extracted {len(paragraphs)} text chunks")
    
    # Create embeddings
    raw_vecs = embed.embed_documents(paragraphs)
    vecs = np.array(raw_vecs, dtype="float32")
    assert vecs.ndim == 2, f"Expected 2D array, got shape {vecs.shape}"
    
    # Build FAISS index
    index = faiss.IndexFlatL2(vecs.shape[1])
    index.add(vecs)
    
    # Save language-specific vector store
    store_dir = f"vector_store/{language}"
    os.makedirs(store_dir, exist_ok=True)
    
    faiss.write_index(index, os.path.join(store_dir, "store.index"))
    with open(os.path.join(store_dir, "text.pkl"), "wb") as f:
        pickle.dump(paragraphs, f)
    
    print(f"  Vector store saved to {store_dir}")

def main(args):
    """Main function supporting both single language and all languages"""
    if args.language:
        # Build for specific language
        build_language_store(args.language, args.docs_path)
    elif args.all:
        # Build for all supported languages
        languages = ["hip", "cuda", "triton"]
        for lang in languages:
            build_language_store(lang)
    elif args.pdf:
        # Legacy support: build for specific PDF (assume HIP)
        print("Legacy mode: building for HIP from specific PDF")
        cfg = yaml.safe_load(open("config.yml"))["rag"]
        embed = get_embedder()
        paragraphs = []
        
        with pdfplumber.open(args.pdf) as pdf_in:
            for p in pdf_in.pages:
                text = p.extract_text()
                if text:
                    paragraphs.extend(chunk(text, cfg["chunk_size"]))
        
        raw_vecs = embed.embed_documents(paragraphs)
        vecs = np.array(raw_vecs, dtype="float32")
        index = faiss.IndexFlatIP(vecs.shape[1])
        index.add(vecs)
        
        store_dir = "vector_store/hip"
        os.makedirs(store_dir, exist_ok=True)
        faiss.write_index(index, os.path.join(store_dir, "store.index"))
        with open(os.path.join(store_dir, "text.pkl"), "wb") as f:
            pickle.dump(paragraphs, f)
    else:
        print("Error: Must specify --language, --all, or --pdf")
        return

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build language-specific vector stores")
    ap.add_argument("--language", choices=["hip", "cuda", "triton"], 
                   help="Build vector store for specific language")
    ap.add_argument("--docs-path", help="Custom path to documentation folder")
    ap.add_argument("--all", action="store_true", 
                   help="Build vector stores for all languages")
    ap.add_argument("--pdf", help="Legacy: build from specific PDF (assumes HIP)")
    
    args = ap.parse_args()
    main(args)
