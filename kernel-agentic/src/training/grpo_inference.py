from __future__ import annotations
import argparse
import json
import os
from typing import Any, Dict, Iterator, List
import yaml
import pandas as pd
import torch
from transformers import GenerationConfig
import textwrap
from tqdm import tqdm
from transformers import AutoModelForCausalLM, AutoTokenizer

from collections import defaultdict

INFER_EVENTS = {"sft_sample_phase1",
                "sft_sample_hpo",
                "iteration_complete"}

CFG = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "../../config.yml"), "r"))
BASE_MODEL_NAME = CFG["training"]["grpo"]["base_model"]

def _events_from_json(obj: Any, module: str | None = None) -> Iterator[Dict[str, Any]]:
    if isinstance(obj, dict) and all(isinstance(v, list) for v in obj.values()):
        # dict-of-lists  → recurse per module
        for mod, events in obj.items():
            for e in events:
                if isinstance(e, dict):
                    e["_module"] = mod
                    yield from _events_from_json(e, mod)
    elif isinstance(obj, list):
        for e in obj:
            yield from _events_from_json(e, module)
    elif isinstance(obj, dict):
        if module and "_module" not in obj:
            obj["_module"] = module
        yield obj


def iter_events(logfile: str) -> Iterator[Dict[str, Any]]:
    with open(logfile, "r", encoding="utf-8") as fp:
        first = fp.read(1)
        fp.seek(0)
        if first in ("{", "["):              # single JSON document
            payload = json.load(fp)
            yield from _events_from_json(payload)
        else:                                # JSONL
            for line in fp:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


# ──────────────────────────────────────────────────────────────────────────────
# Build records for inference
# ──────────────────────────────────────────────────────────────────────────────

def load_records(logfile: str) -> list[dict[str, str]]:
    recs: list[dict[str, str]] = []

    for evt in iter_events(logfile):
        if evt.get("event") not in INFER_EVENTS:
            continue                       # ignore HPO steps / profiler spam
        if "prompt" in evt and "response" in evt:
            recs.append(
                {
                    "input":         evt["prompt"],
                    "ground_truth":  evt["response"],
                    "_module":       evt.get("_module", "root")
                }
            )

    if not recs:
        raise ValueError(f"No usable (prompt, response) pairs in {logfile!r}")
    return recs



# ──────────────────────────────────────────────────────────────────────────────
# Pretty side-by-side printing for terminals
# ──────────────────────────────────────────────────────────────────────────────
def print_side_by_side(df: pd.DataFrame, left_col: str, right_col: str):
    term_w = os.get_terminal_size().columns
    gutter = 4
    col_w  = (term_w - gutter) // 2

    def _wrap(text: str) -> List[str]:
        import textwrap
        return textwrap.wrap(text, width=col_w, replace_whitespace=False) or [""]
    
    # header
    print("```cpp")
    print(f"{left_col:<{col_w}}{' ' * gutter}{right_col:<{col_w}}")
    print(f"{'-'*col_w}{' ' * gutter}{'-'*col_w}")
    # rows
    for _, row in df.iterrows():
        left_lines  = _wrap(row[left_col])
        right_lines = _wrap(row[right_col])
        for i in range(max(len(left_lines), len(right_lines))):
            l = left_lines[i]  if i < len(left_lines)  else ""
            r = right_lines[i] if i < len(right_lines) else ""
            print(f"{l:<{col_w}}{' ' * gutter}{r}")
        print(f"{'-'*col_w}{' ' * gutter}{'-'*col_w}")
    print("```")


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
def main(
    logfile: str,
    model_dir: str,
    output_csv: str,
    device: str,
    base_model: str | None,
    temperature,
    top_p,
    top_k
):
    # 1) records
    records = load_records(logfile)

    # 2) tokenizer (from base model or model_dir)
    base_name = BASE_MODEL_NAME
    tokenizer = AutoTokenizer.from_pretrained(base_name, use_fast=False)
    
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
        tokenizer.pad_token_id = tokenizer.eos_token_id

    # 3) model
    model = AutoModelForCausalLM.from_pretrained(
        model_dir,
        torch_dtype=torch.float16,
        device_map="auto",
    ).to(device).eval()

    gen_conf = GenerationConfig(
        max_new_tokens=2048,
        do_sample=False,
        pad_token_id=tokenizer.pad_token_id,
        # you can override these at the CLI:
        temperature=temperature, 
        top_p=top_p,
        top_k=top_k)
    # 4) generation loop
    outputs = []
    for rec in tqdm(records, desc="Generating"):
        with torch.no_grad():
            inp = tokenizer(rec["input"], return_tensors="pt", truncation=True).to(device)
            gen = model.generate(
                **inp,
                 generation_config=gen_conf,
            )
            generated = gen[0][inp.input_ids.shape[1]:]
            rec["GRPO"] = tokenizer.decode(generated, skip_special_tokens=True)
            outputs.append(rec)

    # 5) save CSV
    df = pd.DataFrame(outputs, columns=["input", "ground_truth", "GRPO"])
    df.to_csv(output_csv, index=False)
    print(f"\nSaved {len(df)} rows → {output_csv}")

    # 6) pretty terminal view (first 3 rows)
    print_side_by_side(df.head(3), "ground_truth", "GRPO")


# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inference with a GRPO-fine-tuned model")
    parser.add_argument("--logfile",   default="logsv0/combined_SFT_generation_history_eval.jsonl", help="JSON/JSONL log file")
    parser.add_argument("--model_dir", default="lora-grpo",                   help="Path to saved GRPO model dir")
    parser.add_argument("--output",    default="grpo_inference.csv",          help="CSV output filename")
    parser.add_argument("--device",    default="cuda:0",                      help="cuda:N or cpu")
    parser.add_argument("--base_model", default=None,
                        help="(optional) base model name for tokenizer if different from model_dir")
    parser.add_argument("--temperature", type=float, default=1.0, help="sampling temperature")
    parser.add_argument("--top_p",       type=float, default=1.0, help="nucleus sampling p")
    parser.add_argument("--top_k",       type=int,   default=50,  help="top-k sampling")
 
    args = parser.parse_args()

    main(
        logfile     = args.logfile,
        model_dir   = args.model_dir,
        output_csv  = args.output,
        device      = args.device,
        base_model  = args.base_model,
        temperature = args.temperature,
        top_p       = args.top_p, 
        top_k       = args.top_k,
    )