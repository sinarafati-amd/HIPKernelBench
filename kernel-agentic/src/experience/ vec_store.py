import pickle, faiss, hashlib, os
def op_hash(torch_expl: str) -> str:
    return hashlib.sha1(torch_expl.encode()).hexdigest()

def add_example(torch_expl, hip_code):
    h = op_hash(torch_expl)
    vec = embed(torch_expl)
    idx, txt = _load()
    idx.add_with_ids(vec, [int(h[:16],16)])
    txt[h] = hip_code
    _save(idx, txt)

def retrieve(torch_expl, k=3):
    h = op_hash(torch_expl)
    vec = embed(torch_expl)
    idx, txt = _load()
    D,I = idx.search(vec, k)
    return [txt[str(i)] for i in I[0] if str(i) in txt]