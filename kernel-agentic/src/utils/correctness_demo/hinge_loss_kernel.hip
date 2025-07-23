#include <hip/hip_runtime.h>
#include <ATen/hip/HIPContext.h>
#include <ATen/ATen.h>

__global__ void hinge_loss_kernel(const float* predictions, const float* targets, float* out, int size) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx < size) {
        out[idx] = fmaxf(0.0f, 1.0f - predictions[idx] * targets[idx]);
    }
}

at::Tensor hinge_loss_rocm(at::Tensor predictions, at::Tensor targets) {
    auto size = predictions.numel();
    auto out = at::zeros_like(predictions);

    const int block_size = 256;
    const int num_blocks = (size + block_size - 1) / block_size;

    hipLaunchKernelGGL(hinge_loss_kernel, dim3(num_blocks), dim3(block_size), 0, 0,
        predictions.data_ptr<float>(), targets.data_ptr<float>(), out.data_ptr<float>(), size);

    return out;
}
