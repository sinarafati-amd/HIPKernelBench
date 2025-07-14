import argparse
import json
import pandas as pd
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel
from tqdm import tqdm
import os
import textwrap

def main(logfile: str, lora_dir: str, output_csv: str, device: str):-
    records = []
    with open(logfile, "r") as f:
        for line in f:
            item = json.loads(line)
            if "prompt" in item and "response" in item:
                records.append({
                    "input": item["prompt"],
                    "ground_truth": item["response"]
                })

    tokenizer = AutoTokenizer.from_pretrained("openlm-research/open_llama_7b", use_fast=False)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
        tokenizer.pad_token_id = tokenizer.eos_token_id

    base = AutoModelForCausalLM.from_pretrained(
        "openlm-research/open_llama_7b", torch_dtype=torch.float16
    )
    model = PeftModel.from_pretrained(base, lora_dir)
    model.to(device).eval()

    outputs = []
    for rec in tqdm(records):
        inp = tokenizer(rec["input"], return_tensors="pt", truncation=True).to(device)
        gen = model.generate(
            **inp, max_new_tokens=2048, do_sample=False,
            pad_token_id=tokenizer.pad_token_id
        )
        generated = gen[0][inp.input_ids.shape[1]:]
        rec["LORA"] = tokenizer.decode(generated, skip_special_tokens=True)
        outputs.append(rec)

    df = pd.DataFrame(outputs, columns=["input","ground_truth","LORA"])
    df.to_csv(output_csv, index=False)
    print(f"\nSaved {len(df)} records to {output_csv}\n")

    # assume df has columns "ground_truth" and "LORA"
    term_width = os.get_terminal_size().columns
    gutter = 4
    col_width = (term_width - gutter) // 2

    def print_pair(gt: str, lr: str):
        # split on real newlines, keep indentation
        left_lines  = gt.splitlines()
        right_lines = lr.splitlines()
        max_lines = max(len(left_lines), len(right_lines))

        for i in range(max_lines):
            left  = left_lines[i]  if i < len(left_lines)  else ""
            right = right_lines[i] if i < len(right_lines) else ""
            # pad each side to exactly col_width
            print(f"{left:<{col_width}}{' ' * gutter}{right}")

    # 2) wrap everything in a code-fence
    print("```cpp")
    # header row
    print(f"{'ground_truth':<{col_width}}{' ' * gutter}{'LORA':<{col_width}}")
    print(f"{'-'*col_width}{' ' * gutter}{'-'*col_width}")

    # 3) loop each example
    for gt, lr in zip(df["ground_truth"], df["LORA"]):
        print_pair(gt, lr)
        print(f"{'-'*col_width}{' ' * gutter}{'-'*col_width}")

    print("```")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--logfile",   type=str, default="logs/combined_SFT_generation_history.jsonl")
    p.add_argument("--lora_dir",  type=str, default="./lora-finetuned")
    p.add_argument("--output",    type=str, default="lora_inference.csv")
    p.add_argument("--device",    type=str, default="cuda:0")
    args = p.parse_args()
    main(args.logfile, args.lora_dir, args.output, args.device)
