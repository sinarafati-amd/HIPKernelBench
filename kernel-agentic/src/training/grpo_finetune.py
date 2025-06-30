from __future__ import annotations
import argparse
import json
import os
import random
import yaml
from typing import Dict, Any, Iterator, List, Tuple

import torch
from datasets import Dataset
from transformers import (
    AutoModelForCausalLM,
    TrainerCallback,
    TrainingArguments,              
)
from peft import PeftModel
from trl import GRPOConfig, GRPOTrainer

from collections import defaultdict

KEEP_EVENTS = {"iteration_complete",
               "sft_sample_phase1",
               "sft_sample_hpo"}          

# ──────────────────────────────────────────────────────────────────────────────
# 1)  Load configs
# ──────────────────────────────────────────────────────────────────────────────
CFG       = yaml.safe_load(open("config.yml"))
GRPO_CFG  = CFG["training"]["grpo"]
LORA_CFG  = CFG["training"]["lora"]
USE_8BIT  = GRPO_CFG.get("use_8bit", False)

_INT_KEYS   = [
    "mini_batch_size", "grad_acc_steps", "num_train_epochs",
    "max_steps", "num_iterations", "logging_steps"
]
_FLOAT_KEYS = [
    "lr", "max_grad_norm", "epsilon", "epsilon_high", "scale_rewards"
]
for k in _INT_KEYS:
    if k in GRPO_CFG and GRPO_CFG[k] is not None:
        GRPO_CFG[k] = int(GRPO_CFG[k])
for k in _FLOAT_KEYS:
    if k in GRPO_CFG and GRPO_CFG[k] is not None:
        GRPO_CFG[k] = float(GRPO_CFG[k])

# ──────────────────────────────────────────────────────────────────────────────
# 2)  Robust log parsing (JSONL, list, or dict-of-modules)
# ──────────────────────────────────────────────────────────────────────────────
def _events_from_json_object(obj: Any, module_name: str | None = None) -> Iterator[Dict[str, Any]]:
    if isinstance(obj, dict) and all(isinstance(v, list) for v in obj.values()):
        for mod, events in obj.items():
            for e in events:
                if isinstance(e, dict):
                    e["_module"] = mod
                    yield from _events_from_json_object(e, mod)
    elif isinstance(obj, list):
        for e in obj:
            yield from _events_from_json_object(e, module_name)
    elif isinstance(obj, dict):
        if module_name is not None and "_module" not in obj:
            obj["_module"] = module_name
        yield obj

def iter_events(logfile: str) -> Iterator[Dict[str, Any]]:
    with open(logfile, "r", encoding="utf-8") as fp:
        start = fp.read(1)
        fp.seek(0)
        if start in ("{", "["):
            payload = json.load(fp)
            yield from _events_from_json_object(payload)
        else:
            for line in fp:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

# ──────────────────────────────────────────────────────────────────────────────
# 3)  Build Dataset
# ──────────────────────────────────────────────────────────────────────────────
# def build_dataset(logfile: str) -> Dataset:
#     rows: list[dict[str, Any]] = []

#     for evt in iter_events(logfile):
#         if evt.get("event") not in KEEP_EVENTS:
#             continue                                   # skip HPO / profile / exec noise
#         if not all(k in evt for k in ("prompt", "response", "speedup", "correct")):
#             continue                                   # guard against partial records

#         rows.append(
#             {
#                 "prompt":     evt["prompt"],
#                 "completion": evt["response"],
#                 "speedup":    float(evt.get("speedup", 0.0) or 0.0),
#                 "correct":    bool(evt.get("correct", False)),
#                 "_module":    evt.get("_module", "root"),   # always non-null
#             }
#         )

#     if not rows:
#         raise ValueError(f"No usable events found in {logfile!r}")
#     random.shuffle(rows)
#     return Dataset.from_list(rows)

def build_dataset(logfile: str) -> Dataset:
    bucket: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"completions": [], "speedup": [], "correct": []}
    )

    for evt in iter_events(logfile):
        if evt.get("event") not in KEEP_EVENTS:
            continue
        if "prompt" not in evt or "response" not in evt:
            continue

        key = evt["prompt"]                      # 1-prompt := 1 bucket
        b   = bucket[key]
        b.setdefault("_module", evt.get("_module", "root"))
        b["prompt"] = key
        b["completions"].append(evt["response"])
        b["speedup"].append(float(evt.get("speedup") or 0.0))
        b["correct"].append(bool(evt.get("correct", False)))

    rows = list(bucket.values())
    if not rows:
        raise ValueError("no usable events in log")

    random.shuffle(rows)
    return Dataset.from_list(rows)
# ──────────────────────────────────────────────────────────────────────────────
# 4)  Reward
# ──────────────────────────────────────────────────────────────────────────────
def speedup_reward(completions, speedup, correct, **_):
    """
    completions : List[str]          – k completions just produced by the model
    speedup     : List[float]        – same length; taken from the dataset row
    correct     : List[bool]         – ditto
    """
    SCALE = 0.25                     # make gradients O(1)
    rewards = []
    for sp, ok in zip(speedup, correct):
        r = SCALE * (sp - 1.0) if ok else -SCALE
        rewards.append(r)
    return rewards
# ──────────────────────────────────────────────────────────────────────────────
# 5)  Loss history callback
# ──────────────────────────────────────────────────────────────────────────────
class LossHistoryCallback(TrainerCallback):
    """
    Collect `loss` values reported by the trainer.  A plot will be created
    at the end of training.
    """
    def __init__(self):
        self.history= []

    def on_log(self, args: TrainingArguments, state, control, logs=None, **kwargs):
        print(logs)
        if not logs:
            return
        key = None
        for k in ("policy_loss", "loss", "train_loss"):
            if k in logs:
                key = k
                break
        if key is not None:
            self.history.append((state.global_step, float(logs[key])))

# ──────────────────────────────────────────────────────────────────────────────
# 6)  Main
# ──────────────────────────────────────────────────────────────────────────────
def main(logfile: str):
    ds = build_dataset(logfile)

    # Trainer config
    training_args = GRPOConfig(
        output_dir                  = GRPO_CFG.get("output_dir", "lora-grpo"),
        logging_steps               = GRPO_CFG.get("logging_steps", 10),
        per_device_train_batch_size = GRPO_CFG["mini_batch_size"],
        gradient_accumulation_steps = GRPO_CFG["grad_acc_steps"],
        learning_rate               = GRPO_CFG["lr"],
        max_grad_norm               = GRPO_CFG["max_grad_norm"],
        num_train_epochs            = GRPO_CFG["num_train_epochs"],
        max_steps                   = GRPO_CFG["max_steps"],
        num_iterations              = GRPO_CFG["num_iterations"],
        epsilon                     = GRPO_CFG["epsilon"],
        epsilon_high                = GRPO_CFG.get("epsilon_high"),
        scale_rewards               = GRPO_CFG["scale_rewards"],
        loss_type                   = GRPO_CFG["loss_type"],
    )

    # Base model
    model = AutoModelForCausalLM.from_pretrained(
        GRPO_CFG["base_model"],
        torch_dtype  = torch.float16,
        load_in_8bit = USE_8BIT,
        device_map   = "auto",
    )

    # Optional LoRA
    lora_dir = LORA_CFG.get("dir", "lora-finetuned")
    if GRPO_CFG.get("use_SFT_model", False) and os.path.isdir(lora_dir):
        try:
            model = PeftModel.from_pretrained(model, lora_dir)
        except RuntimeError as e:
            print(f"[warn] Could not load LoRA adapter from {lora_dir!r}: {e}")

    # Loss tracker
    loss_cb = LossHistoryCallback()

    # Trainer
    trainer = GRPOTrainer(
        model         = model,
        reward_funcs  = speedup_reward,
        args          = training_args,
        train_dataset = ds,
        callbacks     = [loss_cb],
    )

    print(f"▶ Starting GRPO fine-tuning on {len(ds)} samples ({'8-bit' if USE_8BIT else '16-bit'})")
    trainer.train()

    # Save model
    out_dir = training_args.output_dir
    os.makedirs(out_dir, exist_ok=True)
    try:
        model.save_pretrained(out_dir)
        print(f"✔ Model saved to {out_dir}")
    except Exception as e:
        print(f"[error] Saving model failed: {e}")

    # ── Plot loss curve ─────────────────────────────────────────────────────
    if loss_cb.history:
        import matplotlib
        matplotlib.use("Agg")          # headless
        import matplotlib.pyplot as plt

        steps, losses = zip(*loss_cb.history)
        plt.figure(figsize=(8, 5))
        plt.plot(steps, losses)
        plt.title("GRPO Training Loss")
        plt.xlabel("Global step")
        plt.ylabel("Loss")
        plt.grid(True)
        plot_path = os.path.join(out_dir, "loss_curve.png")
        plt.savefig(plot_path, dpi=120, bbox_inches="tight")
        print(f"✔ Loss curve saved to {plot_path}")
    else:
        print("[warn] No loss logs captured — skipping plot")

# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GRPO fine-tuning trainer with loss plotting")
    parser.add_argument(
        "--logfile",
        default="logs/generation_history.json",
        help="Path to JSON/JSONL log file (supports dict-of-modules format)",
    )
    args = parser.parse_args()
    main(args.logfile)
