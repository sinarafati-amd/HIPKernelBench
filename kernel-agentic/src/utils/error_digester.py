import re, textwrap

def digest(err: str, head_n=15) -> str:
    clean = re.sub(r'\x1b\[[0-9;]*[mK]', '', err)        # no ANSI
    lines = clean.splitlines()[:head_n]
    head  = "\n".join(lines)

    if "cannot convert" in head or "invalid operands" in head:
        tag = "TYPE"
    elif "too many registers" in head or "invalid launch" in head:
        tag = "OCCUPANCY"
    elif "syntax error" in head or "expected" in head:
        tag = "SYNTAX"
    else:
        tag = "COMPILATION"
    return f"[{tag}] {textwrap.dedent(head)}"