from __future__ import annotations
import argparse
import json
import os
import random
import yaml
import torch

from datasets import Dataset
from trl import GRPOConfig, GRPOTrainer
from transformers import AutoModelForCausalLM
from peft import PeftModel

# ── Load configs ────────────────────────────────────────────────────────────
CFG       = yaml.safe_load(open("config.yml"))
GRPO_CFG  = CFG["training"]["grpo"]
LORA_CFG  = CFG["training"]["lora"]
USE_8BIT  = GRPO_CFG.get("use_8bit", False)

# ── Cast numeric config values ───────────────────────────────────────────────
int_keys   = ["mini_batch_size", "grad_acc_steps", "num_train_epochs", "max_steps", "num_iterations", "logging_steps"]
float_keys = ["lr", "max_grad_norm", "epsilon", "epsilon_high", "scale_rewards"]
for k in int_keys:
    if k in GRPO_CFG and GRPO_CFG[k] is not None:
        GRPO_CFG[k] = int(GRPO_CFG[k])
for k in float_keys:
    if k in GRPO_CFG and GRPO_CFG[k] is not None:
        GRPO_CFG[k] = float(GRPO_CFG[k])

# ── 1) Build Dataset from iteration_complete rows ───────────────────────────
def build_ds(logfile: str) -> Dataset:
    rows = []
    with open(logfile) as fp:
        for line in fp:
            evt = json.loads(line)
            if evt.get("event") != "iteration_complete":
                continue
            if not all(k in evt for k in ("prompt", "response", "speedup", "correct")):
                continue
            rows.append({
                "prompt":     evt["prompt"],
                "completion": evt["response"],
                "speedup":    evt["speedup"],
                "correct":    evt["correct"],
            })
    random.shuffle(rows)
    return Dataset.from_list(rows)

# ── 2) Define reward function ─────────────────────────────────────────────────
def speedup_reward(completions, speedup, correct, **kwargs):
    return [spd if cor else -1.0 for spd, cor in zip(speedup, correct)]

# ── 3) Main GRPO pipeline ────────────────────────────────────────────────────
def main(logfile: str):
    ds = build_ds(logfile)

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

    # instantiate base model
    base_model = GRPO_CFG["base_model"]
    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        torch_dtype=torch.float16,
        load_in_8bit=USE_8BIT,
        device_map="auto",
    )

    # optionally load LoRA adapter
    lora_dir = LORA_CFG.get("dir", "lora-finetuned")
    if GRPO_CFG.get("use_SFT_model", False) and os.path.isdir(lora_dir):
        try:
            model = PeftModel.from_pretrained(model, lora_dir)
        except RuntimeError as e:
            print(f"Warning: failed to load LoRA adapter from '{lora_dir}': {e}")

    # create and run trainer
    trainer = GRPOTrainer(
        model         = model,
        reward_funcs  = speedup_reward,
        args          = training_args,
        train_dataset = ds,
    )
    print(f"Running GRPO on {len(ds)} samples; 8-bit={'yes' if USE_8BIT else 'no'}")
    trainer.train()

    # save final model (including LoRA) to output_dir
    output_dir = GRPO_CFG.get("output_dir", "lora-grpo")
    os.makedirs(output_dir, exist_ok=True)
    try:
        model.save_pretrained(output_dir)
    except Exception as e:
        print(f"Error saving model to '{output_dir}': {e}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--logfile", default="logs/generation_history.jsonl")
    args = ap.parse_args()
    main(args.logfile)
