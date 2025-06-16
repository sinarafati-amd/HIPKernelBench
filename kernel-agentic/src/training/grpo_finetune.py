from __future__ import annotations
import argparse, json, random, os, yaml
from datasets import Dataset
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel, prepare_model_for_kbit_training
from trl import GRPOConfig, GRPOTrainer

# ─── Load configs ───────────────────────────────────────────────────────
CFG      = yaml.safe_load(open("config.yml"))
GRPO_CFG = CFG["training"]["grpo"]
LORA_CFG = CFG["training"]["lora"]

# ─── Build (prompt, response, reward) dataset ────────────────────────────
def load_offline_dataset(logfile: str) -> Dataset:
    samples = []
    with open(logfile) as fp:
        for line in fp:
            row = json.loads(line)
            if row.get("event") == "iteration_complete":
                # skip if no prompt/response
                if "prompt" not in row or "response" not in row:
                    continue
                reward = row["speedup"] if row["correct"] else -1.0
                samples.append({
                    "prompt":   row["prompt"],
                    "response": row["response"],
                    "reward":   reward,
                })
    random.shuffle(samples)
    return Dataset.from_list(samples)

# ─── Tokenisation helper ─────────────────────────────────────────────────
def build_tokenised(ds: Dataset, tok: AutoTokenizer, max_len: int):
    eos = tok.eos_token
    def _tok(ex):
        text = ex["prompt"] + eos + ex["response"] + eos
        out   = tok(text, truncation=True, max_length=max_len)
        out["rewards"] = [ex["reward"]]
        return out
    return ds.map(_tok, batched=False)

# ─── Main GRPO pipeline ──────────────────────────────────────────────────
def main(logfile: str):
    # 1) Base + (optional) LoRA adapter
    base_name  = LORA_CFG["base_model"]
    adapter_dir = "lora-finetuned"
    tok  = AutoTokenizer.from_pretrained(base_name, use_fast=True)
    tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        base_name,
        load_in_8bit = True,
        device_map   = "auto"
    )
    model = prepare_model_for_kbit_training(model)
    if os.path.isdir(adapter_dir):
        model = PeftModel.from_pretrained(model, adapter_dir)

    # 2) Build RL dataset
    raw_ds = load_offline_dataset(logfile)
    ds     = build_tokenised(raw_ds, tok, max_len=GRPO_CFG["max_tokens"])

    # 3) GRPO config & trainer
    grpo_conf = GRPOConfig(
        ppo_epochs           = GRPO_CFG["ppo_epochs"],
        mini_batch_size      = GRPO_CFG["mini_batch_size"],
        init_kl_coef         = GRPO_CFG["init_kl_coef"],
        target_kl            = GRPO_CFG["target_kl"],
        gamma                = 1.0,
        lam                  = 0.95,
        vf_coef              = GRPO_CFG["vf_coef"],
        cliprange_value      = GRPO_CFG["cliprange_value"],
        learning_rate        = GRPO_CFG["lr"],
        gradient_accumulation_steps = GRPO_CFG["grad_acc_steps"],
        max_grad_norm        = 1.0,
    )

    trainer = GRPOTrainer(
        model      = model,
        ref_model  = None,           # KL to initial weights
        tokenizer  = tok,
        dataset    = ds,
        config     = grpo_conf,
        reward_key = "rewards",
    )

    print(f"Dataset size: {len(ds)} samples → starting GRPO…")
    trainer.train(GRPO_CFG["num_steps"])
    trainer.save_pretrained("lora-grpo")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--logfile", default="logs/generation_history.jsonl")
    main(ap.parse_args().logfile)
