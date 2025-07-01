# HIPKernelBench

An AI-powered framework for automatically generating, analyzing, and optimizing GPU kernels from PyTorch code.

## Project Overview

HIPKernelBench is a comprehensive system designed to automatically translate PyTorch operations into optimized GPU kernels using an agentic AI approach. The system leverages large language models (LLMs) to analyze PyTorch code, generate equivalent GPU implementations, and optimize them for performance using genetic or Bayesian search methods.

The framework follows a two-phase approach:
1. **Phase 1 - LLM Synthesis**: Analyze PyTorch code and generate functionally correct HIP kernels
2. **Phase 2 - Optimization**: Apply Bayesian or genetic optimization techniques to fine-tune kernel parameters

## Repository Structure

```
.
├── kernel-agentic/              # Main codebase
│   ├── config.yml              # Configuration settings for the system
│   ├── requirements.txt        # Python dependencies
│   ├── Makefile               # Build and run automation
│   ├── src/                    # Source code
│   │   ├── agents/             # AI agent components
│   │   │   ├── torch_analyser.py    # Analyzes PyTorch code
│   │   │   ├── kernel_generator.py  # Generates HIP kernels
│   │   │   ├── orchestrator.py      # Main workflow coordinator
│   │   │   ├── rag_researcher.py    # Retrieval-augmented generation agent
│   │   │   ├── executor.py          # Executes and tests kernels
│   │   │   ├── search_agent.py      # Handles optimization search
│   │   │   └── feedback_analyzer.py # Analyzes execution feedback
│   │   ├── data/               # Data handling utilities
│   │   ├── eval/               # Evaluation tools
│   │   ├── optim/              # Optimization algorithms
│   │   │   ├── bayes.py        # Bayesian optimization
│   │   │   └── genetic.py      # Genetic algorithm optimization
│   │   ├── training/           # Training infrastructure
│   │   │   ├── lora_finetune.py    # LoRA fine-tuning
│   │   │   └── grpo_finetune.py    # GRPO reinforcement learning
│   │   └── utils/              # Utility functions
│   ├── scripts/                # Helper scripts
│   ├── torch_codes/            # PyTorch code examples
│   ├── logs/                   # Generation logs and results
│   ├── docs/                   # Documentation
│   └── vector_store/           # Vector embeddings for RAG
```

## Key Components

### Agents

- **Orchestrator**: Coordinates the overall workflow
- **TorchAnalyser**: Analyzes PyTorch code to understand its functionality
- **RAGResearcher**: Retrieves relevant documentation and examples
- **KernelGenerator**: Generates HIP kernel code
- **Executor**: Compiles, runs, and profiles kernel performance
- **SearchAgent**: Coordinates optimization search algorithms

### Optimization

- **BayesOpt**: Bayesian optimization for kernel parameters
- **GeneticOpt**: Genetic algorithm for parameter optimization

### Training

- **LoRA Finetuning**: Fine-tuning LLMs with Low-Rank Adaptation
- **GRPO Finetuning**: Gradient-based Reinforcement Learning from Policy Optimization

## Installation and Setup

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd HIPKernelBench/kernel-agentic
   ```

2. Install dependencies:
   ```bash
   make dev-setup
   ```

## Usage Instructions

### Building Vector Store

Create the vector store for RAG from documentation you can pass one of the languages [hip,cuda, triton] but default is hip:

```bash
make vector-store
```

To specify a different language (default is HIP):

```bash
make vector-store lang=cuda
```

### Running the System

Run the system on all PyTorch files in the torch_codes directory:

```bash
make run
```

This will:
1. Process each PyTorch file
2. Generate equivalent kernels
3. Save generation history to logs/

### Fine-tuning LLMs

Combine generation histories for training:

```bash
make combine-history
```

Train a LoRA adapter on the generation data:

```bash
make train-lora
```

Train with GRPO (Gradient-based Reinforcement Learning):

```bash
make grpo-finetune
```

### Performance Analysis

Generate baseline timing information:

```bash
make baseline-time TORCH_FILE=path/to/torch_file.py
```

Run and check correctness against PyTorch:

```bash
make run-check HIP_BIN=path/to/binary TORCH_FILE=path/to/torch_file.py
```


## Configuration

The system is configured through the `config.yml` file with the following key sections:

- **Pipeline**: Configuration for the overall workflow
- **openai**: API settings for LLM access
- **gpu_specs**: Target GPU specifications ---> not required as gpu_spec is also being extracted from rocm api
- **eval**: Evaluation parameters
- **training**: LoRA and GRPO fine-tuning parameters


