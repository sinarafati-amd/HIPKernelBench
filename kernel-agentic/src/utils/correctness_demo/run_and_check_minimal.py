import time
import importlib.util
import torch


def load_model_module(path):
    spec = importlib.util.spec_from_file_location("model_module", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def get_model_instance(module):
    if hasattr(module, "ModelNew"):
        return module.ModelNew
    elif hasattr(module, "Model"):
        return module.Model
    else:
        raise RuntimeError("Neither Model nor ModelNew class found in module.")


def benchmark(model, get_inputs_fn, device="cuda", num_iters=3, label=""):
    times = []
    for _ in range(5):  # warmup
        inputs = get_inputs_fn()
        model(*[x.to(device) for x in inputs])

    torch.cuda.synchronize()
    for _ in range(num_iters):
        inputs = get_inputs_fn()
        inputs = [x.to(device) for x in inputs]
        start = time.time()
        with torch.no_grad():
            _ = model(*inputs)
        torch.cuda.synchronize()
        end = time.time()
        times.append(end - start)

    avg = sum(times) / len(times)
    print(f"[{label}] Avg Time over {num_iters} iters: {avg * 1000:.4f} ms")
    return avg


def compare_outputs(model_ref, model_kernel, ref_get_inputs, ker_get_inputs, device="cuda"):
    torch.manual_seed(42)  # Set random seed
    inputs_ref = ref_get_inputs()
    inputs_ref = [x.to(device) for x in inputs_ref]

    torch.manual_seed(42)  # Reset random seed
    inputs_kernel = ker_get_inputs()
    inputs_kernel = [x.to(device) for x in inputs_kernel]

    model_ref.eval()
    model_kernel.eval()

    with torch.no_grad():
        out_ref = model_ref(*inputs_ref)
        torch.cuda.synchronize()
        out_kernel = model_kernel(*inputs_kernel)
        torch.cuda.synchronize()

    if torch.allclose(out_ref, out_kernel, atol=1e-2, rtol=1e-3):
        print("[✔] Output match: torch.allclose PASSED")
    else:
        max_diff = (out_ref - out_kernel).abs().max().item()
        print("[✘] Output mismatch")
        print(f"Max diff: {max_diff:.6f}, Ref: {out_ref.item():.6f}, Kernel: {out_kernel.item():.6f}")



def main(ref_path, kernel_path):
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    # Load reference and kernel model modules
    ref_module = load_model_module(ref_path)
    kernel_module = load_model_module(kernel_path)

    # Extract model classes and input functions
    ref_model_cls = get_model_instance(ref_module)
    kernel_model_cls = get_model_instance(kernel_module)

    # Instantiate models
    model_ref = ref_model_cls().to(device)
    model_kernel = kernel_model_cls().to(device)

    ref_time = benchmark(model_ref, ref_module.get_inputs, device=device, label="PyTorch Ref")
    kernel_time = benchmark(model_kernel, kernel_module.get_inputs, device=device, label="ROCm Kernel")
    print(f"🚀 Speedup: {ref_time / kernel_time:.2f}x")

    print("\n🔍 Checking output correctness...")
    compare_outputs(model_ref, model_kernel, ref_module.get_inputs, kernel_module.get_inputs, device)

    del model_ref, model_kernel
    torch.cuda.empty_cache()


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("Usage: python run_and_check_minimal.py <ref_model.py> <kernel_model.py>")
        sys.exit(1)

    ref_path = sys.argv[1]
    kernel_path = sys.argv[2]
    main(ref_path, kernel_path)

