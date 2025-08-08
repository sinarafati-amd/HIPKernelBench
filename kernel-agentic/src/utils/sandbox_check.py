import argparse, json, sys, torch
from pathlib import Path

# Import hip_forward from correctness
from src.eval.correctness import hip_forward


def try_case(so_path: str, case_id: int) -> bool:
    torch.manual_seed(0)
    device = "cuda"
    try:
        if case_id == 1:
            # 1D single input
            x = torch.randn(1024, device=device, dtype=torch.float32)
            out, _ = hip_forward(so_path, [x], expected_output=x)
            return out.shape == x.shape
        if case_id == 2:
            # 1D two inputs
            a = torch.randn(1024, device=device, dtype=torch.float32)
            b = torch.randn(1024, device=device, dtype=torch.float32)
            c = torch.empty_like(a)
            out, _ = hip_forward(so_path, [a, b], expected_output=c)
            return out.shape == c.shape
        if case_id == 3:
            # 2D matmul-ish shape (square)
            N = 128
            a = torch.randn(N, N, device=device, dtype=torch.float32)
            b = torch.randn(N, N, device=device, dtype=torch.float32)
            c = torch.empty(N, N, device=device, dtype=torch.float32)
            out, _ = hip_forward(so_path, [a, b], expected_output=c)
            return out.shape == c.shape
        if case_id == 4:
            # 2D rectangular variant (M,K) x (K,N) = (M,N)
            M, K, N = 128, 64, 96
            a = torch.randn(M, K, device=device, dtype=torch.float32)
            b = torch.randn(K, N, device=device, dtype=torch.float32)
            c = torch.empty(M, N, device=device, dtype=torch.float32)
            out, _ = hip_forward(so_path, [a, b], expected_output=c)
            return out.shape == c.shape
    except Exception:
        return False
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--so", required=True, help="Path to shared object file")
    args = ap.parse_args()
    so_path = args.so

    if not Path(so_path).exists():
        print(json.dumps({"ok": False, "error": "shared object not found"}))
        return 2

    # Try cases; if any works, declare success
    for cid in (1, 2, 3, 4):
        if try_case(so_path, cid):
            print(json.dumps({"ok": True, "case": cid}))
            return 0

    print(json.dumps({"ok": False, "error": "no signature matched"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())


