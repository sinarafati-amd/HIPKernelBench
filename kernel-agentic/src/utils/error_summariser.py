def summarise(stderr: str, max_tokens=200):
    # trivial heuristic: keep first & last 10 lines + any "error:"
    lines = stderr.splitlines()
    out = lines[:10]
    out += [l for l in lines if "error:" in l][:20]
    out += lines[-10:]
    return "\n".join(out)[:max_tokens*4]   # 4 char ≈ 1 token