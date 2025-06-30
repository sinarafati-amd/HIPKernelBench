import argparse, json, yaml, os, random, torch
from datasets import Dataset
from transformers import AutoTokenizer, AutoModelForCausalLM, Trainer, TrainingArguments, DataCollatorForLanguageModeling
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
import matplotlib.pyplot as plt
CFG = yaml.safe_load(open("config.yml"))["training"]["lora"]



def load_logs(logfile):
    valid_events = {"sft_sample", "sft_sample_hpo", "sft_sample_phase1"}
    with open(logfile) as fp:
        rows = [json.loads(l) for l in fp if any(event in l for event in valid_events)]
    return Dataset.from_list(
        [{"prompt": r["prompt"], "response": r["response"]} for r in rows]
    )

def main(logfile):
    ds = load_logs(logfile)

    # Load tokenizer/model first
    tok = AutoTokenizer.from_pretrained(CFG["base_model"], use_fast=False)
    
    # Fix: Set padding token for LLaMA tokenizer
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
        tok.pad_token_id = tok.eos_token_id
    
    model = AutoModelForCausalLM.from_pretrained(
        CFG["base_model"], 
        torch_dtype=torch.float16, 
        device_map={ "": "cuda:0" }
    )

    # Resize token embeddings if we added new tokens
    model.resize_token_embeddings(len(tok))

    # Configure LoRA
    lora_cfg = LoraConfig(
        r=CFG["r"], 
        lora_alpha=CFG["alpha"], 
        target_modules=CFG["target_modules"],
        lora_dropout=0.1,  
        bias="none",
        task_type="CAUSAL_LM"
    )
    
    # Prepare model for k-bit training if using quantization
    model = prepare_model_for_kbit_training(model)
    model = get_peft_model(model, lora_cfg)

    # Define tokenization function
    def tokenize(batch):
        texts = [
            p + tok.bos_token + r + tok.eos_token  # Add EOS token at the end
            for p, r in zip(batch["prompt"], batch["response"])
        ]
        # Use max_length instead of padding="longest" for better control
        return tok(
            texts, 
            truncation=True, 
            padding="max_length",
            max_length=512,  
            return_tensors="pt"
        )

    model.config.use_cache = False
    model.enable_input_require_grads()
    # Tokenize dataset
    ds = ds.map(tokenize, batched=True, remove_columns=["prompt", "response"])
    
    # Set up training arguments
    training_args = TrainingArguments(
        output_dir="./lora-finetuned",
        num_train_epochs=CFG["epochs"],
        per_device_train_batch_size=4,  
        gradient_accumulation_steps=4,
        learning_rate=5e-4,
        logging_steps=10,
        save_steps=500,
        save_total_limit=2,
        remove_unused_columns=False,
        dataloader_drop_last=True,
        warmup_steps=100,
        weight_decay=0.01,
        fp16=True,  # Use mixed precision for memory efficiency
        report_to=None,  # Disable wandb/tensorboard if not needed
    )
    
    # Data collator for language modeling
    data_collator = DataCollatorForLanguageModeling(
        tokenizer=tok,
        mlm=False,  # We're doing causal LM, not masked LM
        pad_to_multiple_of=8,  # For efficiency
    )
    
    # Initialize trainer
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=ds,
        data_collator=data_collator,
    )
    
    # Enable gradient checkpointing for memory efficiency
    model.gradient_checkpointing_enable()
    
    # Start training
    train_output = trainer.train()
    
    # Save the model
    trainer.save_model()
    tok.save_pretrained("./lora-finetuned")

    log_history = trainer.state.log_history

    epochs = [entry["epoch"] for entry in log_history if "loss" in entry]
    losses = [entry["loss"] for entry in log_history if "loss" in entry]

    # Plot
    plt.figure(figsize=(6,4))
    plt.plot(epochs, losses, marker="o")
    plt.xlabel("Epoch")
    plt.ylabel("Training Loss")
    plt.title("Loss Trend Across Epochs")
    plt.grid(True)

    # Save to disk
    plot_path = os.path.join(training_args.output_dir, "loss_trend.png")
    plt.savefig(plot_path)
    plt.close()

    print(f"➡️ Saved loss plot to {plot_path}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--logfile", default="logs/combined_SFT_generation_history.jsonl")
    main(ap.parse_args().logfile)