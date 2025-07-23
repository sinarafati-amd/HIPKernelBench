## Demo PyTorch vs HIP HingeLoss Benchmark

This script compares the **performance** and **numerical accuracy** between:

- A **PyTorch native** Hinge Loss model (`Model`), and
- A **ROCm custom kernel** version (`ModelNew`).

It reports timing results and checks if both implementations produce equivalent outputs.

---

## Files

- `run_and_check_minimal.py` — Main benchmarking script
- `100_HingeLoss.py` — ROCm custom kernel model
- `<ref_model>.py` — Reference PyTorch model

---

## ▶️ Usage

```bash
python run_and_check_minimal.py ./100_HingeLoss.py ./100_HingeLoss_new.py
