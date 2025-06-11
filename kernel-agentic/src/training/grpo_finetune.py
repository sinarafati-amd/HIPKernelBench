"""
GRPO fine-tune on generation_history.jsonl
==========================================

• Starts from the *SFT-LoRA adapter* you trained earlier
  (folder: `lora-finetuned/`).  If you have none, it will
  fall back to the base model.

• Offline RL:  reward signals are mined from logs:
    –   iteration_complete →  reward =  speedup   (if correct=True)
    –   else →  reward = −1.0                    (penalise failures)

• Uses HF TRL's `GRPOTrainer`, which implements Generalised
  Reinforcement Policy Optimisation (actor-critic with a KL
  controller, see https://laion.ai/blog/grpo for theory).
"""

from __future__ import annotations
import argparse, json, math, os, random, yaml
from datasets import Dataset
import torch, transformers, accelerate
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel, LoraConfig, prepare_model_for_kbit_training
from trl import GRPOConfig, GRPOTrainer

CFG      = yaml.safe_load(open("config.yml"))
GRPO_CFG = CFG["training"]["grpo"]
LORA_CFG = CFG["training"]["lora"]

# ---------------------------------------------------------------------
# 1)  Build (prompt, response, reward) dataset from JSONL
# ---------------------------------------------------------------------
def load_offline_dataset(logfile: str) -> Dataset:
    samples = []
    with open(logfile) as fp:
        for line in fp:
            row = json.loads(line)
            if row.get("event") == "iteration_complete":
                prompt    = row["prompt"]
                response  = row["response"]
                correct   = row.get("correct", False)
                speedup   = row.get("speedup", 0.0) or 0.0
                reward    = speedup if correct else -1.0
                samples.append({"prompt": prompt, "response": response,
                                "reward": reward})
    random.shuffle(samples)
    return Dataset.from_list(samples)

# ---------------------------------------------------------------------
# 2)  Tokenisation helper
# ---------------------------------------------------------------------
def build_tokenised(ds: Dataset, tok: AutoTokenizer, max_len: int):
    eos = tok.eos_token
    def _tok(example):
        text = example["prompt"] + eos + example["response"]
        tokens = tok(text, truncation=True, max_length=max_len)
        tokens["rewards"] = [example["reward"]]   # scalar reward per sample
        return tokens
    return ds.map(_tok, batched=False)

# ---------------------------------------------------------------------
# 3)  Main GRPO pipeline
# ---------------------------------------------------------------------
def main(logfile: str):
    # 3.1  Model & tokenizer ----------------------------------------------------
    base_name   = LORA_CFG["base_model"]
    sf_adapter  = "lora-finetuned"
    tok         = AutoTokenizer.from_pretrained(base_name, use_fast=True)
    tok.pad_token = tok.eos_token
    model       = AutoModelForCausalLM.from_pretrained(
                      base_name, load_in_8bit=True, device_map="auto")
    model       = prepare_model_for_kbit_training(model)
    if os.path.isdir(sf_adapter):
        model = PeftModel.from_pretrained(model, sf_adapter)

    # 3.2  Offline RL dataset ---------------------------------------------------
    raw_ds  = load_offline_dataset(logfile)
    ds      = build_tokenised(raw_ds, tok, max_len=GRPO_CFG["max_tokens"])

    # 3.3  GRPO trainer ---------------------------------------------------------
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
        model            = model,
        ref_model        = None,            # use KL against initial weights
        tokenizer        = tok,
        dataset          = ds,
        config           = grpo_conf,
        reward_key       = "rewards",
    )

    print(f"Dataset size: {len(ds)} prompts — starting GRPO fine-tune…")
    trainer.train(GRPO_CFG["num_steps"])
    trainer.save_pretrained("lora-grpo")

# ---------------------------------------------------------------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--logfile", default="logs/generation_history.jsonl")
    main(ap.parse_args().logfile)
