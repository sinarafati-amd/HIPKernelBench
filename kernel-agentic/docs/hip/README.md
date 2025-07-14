# HIP Documentation

This folder should contain HIP-specific documentation files (PDFs) for RAG retrieval.

## Current files:
- hip_spec.pdf (moved from docs/ root)

## To add more documentation:
1. Place any HIP-related PDF files in this folder
2. Run: `python -m src.data.build_vector_store --language hip`

The vector store will be built from all PDF files in this directory.
