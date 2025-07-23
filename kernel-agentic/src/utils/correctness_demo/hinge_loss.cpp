#include <torch/extension.h>

at::Tensor hinge_loss_rocm(at::Tensor predictions, at::Tensor targets);

PYBIND11_MODULE(TORCH_EXTENSION_NAME, m) {
    m.def("hinge_loss_rocm", &hinge_loss_rocm, "ROCm Hinge Loss");
}
