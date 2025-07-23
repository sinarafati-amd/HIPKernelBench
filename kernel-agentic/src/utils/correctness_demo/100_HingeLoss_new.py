import torch
import torch.nn as nn
from torch.utils.cpp_extension import load_inline, load
import os

'''
# Define the custom ROCm kernel for Hinge Loss
hinge_loss_source = """
#include <torch/extension.h>
#include <hip/hip_runtime.h>
#include <ATen/hip/HIPContext.h>

__global__ void hinge_loss_kernel(const float* predictions, const float* targets, float* out, int size) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx < size) {
        out[idx] = fmaxf(0.0f, 1.0f - predictions[idx] * targets[idx]);
    }
}

torch::Tensor hinge_loss_rocm(torch::Tensor predictions, torch::Tensor targets) {
    auto size = predictions.numel();
    auto out = torch::zeros_like(predictions);

    const int block_size = 256;
    const int num_blocks = (size + block_size - 1) / block_size;

    hinge_loss_kernel<<<num_blocks, block_size>>>(predictions.data_ptr<float>(), targets.data_ptr<float>(), out.data_ptr<float>(), size);

    return out;
}
"""

hinge_loss_cpp_source = "torch::Tensor hinge_loss_rocm(torch::Tensor predictions, torch::Tensor targets);"

# Compile the inline ROCm code for Hinge Loss
hinge_loss = load_inline(
    name='hinge_loss',
    cpp_sources=hinge_loss_cpp_source,
    cuda_sources=hinge_loss_source,
    functions=['hinge_loss_rocm'],
    verbose=True,
    extra_cflags=['-I/opt/rocm/include'],
    extra_cuda_cflags=['-I/opt/rocm/include'],
    extra_ldflags=['-L/opt/rocm/lib', "-lamdhip64"],
)
'''

hinge_loss = load(
    name="hinge_loss",
    sources=["hinge_loss.cpp", "hinge_loss_kernel.hip"],
    extra_cflags=['-I/opt/rocm/include'],
    extra_cuda_cflags=['-I/opt/rocm/include'],
    extra_ldflags=['-L/opt/rocm/lib', "-lamdhip64"],
    build_directory="./build_cache", # Ensure build directory exists
    verbose=True
)

class ModelNew(nn.Module):
    def __init__(self):
        super(ModelNew, self).__init__()
        self.hinge_loss = hinge_loss

    def forward(self, predictions, targets):
        loss = self.hinge_loss.hinge_loss_rocm(predictions, targets)
        return torch.mean(loss)

"""
hardware AMD Instinct MI300X
runtime 0.0335
"""

batch_size = 1024
input_shape = (1024,)
dim = 1

def get_inputs():
    predictions = torch.rand(batch_size, *input_shape, dtype=torch.float32, device="cuda")
    targets = torch.randint(0, 2, (batch_size, *input_shape), dtype=torch.float32, device="cuda") * 2 - 1
    return [predictions, targets]

def get_init_inputs():
    return []
