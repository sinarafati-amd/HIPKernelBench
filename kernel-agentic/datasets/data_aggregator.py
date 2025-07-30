from datasets import load_dataset, Dataset


def AI_CUDA_Engineer_build_samples(split="level_1"):
    ds = load_dataset("SakanaAI/AI-CUDA-Engineer-Archive", split=split)
    breakpoint()
    ds = ds.filter(lambda r: r["Correct"] and r["Error"] is None)
    def to_prompt(r):
        pt_code = r["PyTorch_Code_Functional"] or r["PyTorch_Code_Module"]
        prompt = f"""<|user|>\nYou are a CUDA kernel engineer. Task: Implement **{r['Op_Name']}**…\n```python\n{pt_code}\n```\n\n<|assistant|>\n"""
        return {"prompt": prompt, "completion": f"```cpp\n{r['CUDA_Code']}\n```"}
    return ds.map(to_prompt, remove_columns=ds.column_names)

train_ds = AI_CUDA_Engineer_build_samples("level_1").train_test_split(test_size=0.05, seed=42)