# CUDA Documentation

This folder should contain CUDA-specific documentation files (PDFs) for RAG retrieval.

## To add documentation:
1. Download CUDA programming guides, best practices, etc. as PDF files
2. Place them in this folder
3. Run: `python -m src.data.build_vector_store --language cuda`

## Suggested CUDA documentation:
- CUDA C++ Programming Guide
- CUDA C++ Best Practices Guide  
- CUDA Runtime API Reference
- Thrust User Guide
- cuBLAS/cuDNN documentation

The vector store will be built from all PDF files in this directory.
