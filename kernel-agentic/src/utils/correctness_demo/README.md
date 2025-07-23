## Demo PyTorch vs HIP HingeLoss Benchmark

This script compares the **performance** and **numerical accuracy** between:

- A **PyTorch native** Hinge Loss model (`Model`), and
- A **ROCm custom kernel** version (`ModelNew`).

It reports timing results and checks if both implementations produce equivalent outputs.

---

## Files

- `run_and_check_minimal.py` — Main benchmarking script
- `100_HingeLoss.py` — Reference PyTorch model
- `100_HingeLoss_new.py` — ROCm custom kernel model
- `hinge_loss.cpp` — PyTorch custom op using pybind
- `hinge_loss_kernel.hip` — Kernel code with tensor entry

---

## ▶️ Usage

```bash
python run_and_check_minimal.py ./100_HingeLoss.py ./100_HingeLoss_new.py
