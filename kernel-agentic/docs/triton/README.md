# Triton Documentation

This folder should contain Triton-specific documentation files (PDFs) for RAG retrieval.

## To add documentation:
1. Download Triton tutorials, documentation as PDF files
2. Place them in this folder  
3. Run: `python -m src.data.build_vector_store --language triton`

## Suggested Triton documentation:
- Triton Python Tutorial PDFs
- Triton Language Reference
- Triton GPU Programming Guide
- Performance optimization guides

## Note:
Since Triton documentation is often in HTML/Markdown format online, you may need to:
1. Convert web pages to PDF using browser "Print to PDF"
2. Download any available PDF documentation from the Triton project

The vector store will be built from all PDF files in this directory.
