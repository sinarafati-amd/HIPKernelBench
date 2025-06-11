import argparse, json, yaml, os, random, torch
from datasets import Dataset
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

CFG = yaml.safe_load(open("config.yml"))["training"]["lora"]

def load_logs(logfile):
    with open(logfile) as fp:
        rows = [json.loads(l) for l in fp if "hip_generator" in l]
    samples = [{"prompt": r["prompt"], "response": r["response"]} for r in rows]
    return Dataset.from_list(samples)

def main(logfile):
    ds = load_logs(logfile)
    tok = AutoTokenizer.from_pretrained(CFG["base_model"], use_fast=True)
    m = AutoModelForCausalLM.from_pretrained(CFG["base_model"], load_in_8bit=True, device_map="auto")
    m = prepare_model_for_kbit_training(m)
    lora_cfg = LoraConfig(r=CFG["r"], lora_alpha=CFG["alpha"], target_modules=CFG["target_modules"])
    m = get_peft_model(m, lora_cfg)
    def tokenize(batch):
        return tok(batch["prompt"] + tok.bos_token + batch["response"], truncation=True)
    m.train()
    m.gradient_checkpointing_enable()
    m.enable_input_require_grads()
    ds = ds.map(tokenize, batched=True, remove_columns=["prompt","response"])
    m.compile()
    m.fit(train_dataset=ds, num_train_epochs=CFG["epochs"])
    m.save_pretrained("lora-finetuned")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--logfile", default="logs/generation_history.jsonl")
    main(ap.parse_args().logfile)
