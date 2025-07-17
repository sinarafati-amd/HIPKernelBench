# Prompts Used in This Run

*Generated on: 2025-07-17 18:14:00*

This folder contains all the prompts that were used during this optimization run. These prompts define how the AI models analyze PyTorch code and generate optimized kernels.

## 📝 Prompt Files

### 🌱 Naive Generation Prompts
These prompts are used for initial kernel generation:
- [`hip_naive.txt`](./hip_naive.txt)

### ⚡ Optimization Prompts
These prompts are used for performance optimization:
- [`hip_opt.txt`](./hip_opt.txt)
- [`hip_optimize.txt`](./hip_optimize.txt)
- [`optimization.txt`](./optimization.txt)

### 🔧 Error Handling Prompts
These prompts are used for fixing compilation/runtime errors:
- [`error_refine.txt`](./error_refine.txt)

### 🔄 Refinement Prompts
These prompts are used for iterative improvements:
- [`refinement.txt`](./refinement.txt)

## 🎯 Usage Notes

- **Naive prompts**: Initial code generation from PyTorch
- **Optimization prompts**: Performance tuning and advanced optimizations  
- **Error prompts**: Debugging and fixing generated code
- **Refinement prompts**: Iterative improvement based on performance results

Each prompt file contains specific instructions that guide the AI models in their respective tasks during the kernel optimization process.
