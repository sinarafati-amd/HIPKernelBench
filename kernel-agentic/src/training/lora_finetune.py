import argparse, json, yaml, os, random, torch
import mlflow
from mlflow.models import infer_signature
import numpy as np
from datasets import Dataset
from transformers import AutoTokenizer, AutoModelForCausalLM, Trainer, TrainingArguments, DataCollatorForLanguageModeling
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training, PeftModel
import matplotlib.pyplot as plt
import os
from dotenv import load_dotenv
from transformers.integrations import MLflowCallback
from transformers.integrations import MLflowCallback as TransformersMLflowCallback
import time
from difflib import SequenceMatcher
import pandas as pd
from tqdm import tqdm
from sklearn.manifold import TSNE
from sklearn.decomposition import PCA
import seaborn as sns
import os 
import sys 
from pathlib import Path
dir_path=str(Path(os.path.dirname(Path(__file__))).parent)
if dir_path not in sys.path:
    sys.path.append(dir_path)

from huggingface_hub import login
from agents.executor import Executor
from data.embeddings import get_embedder
from dotenv import load_dotenv
import urllib3
urllib3.disable_warnings(urllib3.exceptions.HeaderParsingError)

load_dotenv()
import urllib3
urllib3.disable_warnings(urllib3.exceptions.HeaderParsingError)
CFG = yaml.safe_load(open("config.yml"))["training"]["lora"]
huggingface_read = CFG["huggingface_read"]
login(huggingface_read)
# Configure MLflow
MLFLOW_TRACKING_URI = "http://localhost:5000"
mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)

def load_eval_dataset(eval_file):
    """Load evaluation dataset from JSONL file, supporting both prompt/response and original_kernel/optimized_kernel formats"""
    eval_data = []
    with open(eval_file, 'r') as f:
        for line in f:
            data = json.loads(line)
            # Check which format the data is in
            if 'prompt' in data and 'response' in data:
                # Original format
                eval_data.append({
                    'prompt': data['prompt'],
                    'ground_truth': data['response']
                })
            elif 'original_kernel' in data and 'optimized_kernel' in data:
                # Alternative format
                eval_data.append({
                    'prompt': data['original_kernel'],
                    'ground_truth': data['optimized_kernel']
                })
            else:
                print(f"Warning: Skipping record with unknown format: {data.keys()}")
    return eval_data

def generate_responses(model, tokenizer, eval_data, device="cuda:0"):
    """Generate responses using the fine-tuned model"""
    model.eval()
    generated_responses = []
    
    with torch.no_grad():
        for item in tqdm(eval_data, desc="Generating responses"):
            # Tokenize input
            inputs = tokenizer(
                item['prompt'], 
                return_tensors="pt", 
                truncation=True, 
                max_length=512
            ).to(device)
            
            # Generate response
            outputs = model.generate(
                **inputs,
                max_new_tokens=1024,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id,
            )
            
            # Decode generated text (exclude input tokens)
            generated = outputs[0][inputs.input_ids.shape[1]:]
            response = tokenizer.decode(generated, skip_special_tokens=True)
            
            generated_responses.append({
                'prompt': item['prompt'],
                'ground_truth': item['ground_truth'],
                'generated': response.strip()
            })
    
    return generated_responses

def calculate_fuzzy_similarity(text1, text2):
    """Calculate fuzzy similarity between two texts using SequenceMatcher"""
    return SequenceMatcher(None, text1, text2).ratio()

def get_code_embeddings(texts, embedder):
    """Get embeddings for code texts"""
    embeddings = []
    for text in tqdm(texts, desc="Getting embeddings"):
        try:
            embedding = embedder.embed_query(text)
            embeddings.append(embedding)
        except Exception as e:
            print(f"Error getting embedding: {e}")
            # Use zero vector as fallback
            embeddings.append([0.0] * 1536)  # Assuming embedding dimension
    return np.array(embeddings)

def plot_embedding_comparison(ground_truth_embeddings, generated_embeddings, output_dir):
    """Plot comparison of embeddings using t-SNE"""
    if TSNE is None:
        print("Warning: scikit-learn not available, skipping embedding plot")
        return None
        
    try:
        # Combine embeddings
        all_embeddings = np.vstack([ground_truth_embeddings, generated_embeddings])
        labels = ['Ground Truth'] * len(ground_truth_embeddings) + ['Generated'] * len(generated_embeddings)
        
        # Apply t-SNE
        tsne = TSNE(n_components=2, random_state=42, perplexity=min(30, len(all_embeddings)-1))
        embeddings_2d = tsne.fit_transform(all_embeddings)
        
        # Create plot
        plt.figure(figsize=(12, 8))
        for label in ['Ground Truth', 'Generated']:
            mask = np.array(labels) == label
            plt.scatter(embeddings_2d[mask, 0], embeddings_2d[mask, 1], 
                       label=label, alpha=0.6, s=50)
        
        plt.xlabel('t-SNE Component 1')
        plt.ylabel('t-SNE Component 2')
        plt.title('Code Embeddings: Ground Truth vs Generated')
        plt.legend()
        plt.grid(True, alpha=0.3)
        
        plot_path = os.path.join(output_dir, "embedding_comparison.png")
        plt.savefig(plot_path, dpi=300, bbox_inches='tight')
        plt.close()
        
        return plot_path
    except Exception as e:
        print(f"Error creating embedding plot: {e}")
        return None

def evaluate_kernel_compilation(responses, executor):
    """Evaluate kernel compilation success rate (robust to compile errors)."""
    compilation_results = []
    execution_times = []

    for resp in tqdm(responses, desc="Evaluating kernels"):
        # ---- ground truth ----
        try:
            gt_stats, gt_error, gt_file = executor.run(resp['ground_truth'])
            gt_compiles = (gt_error is None)
        except Exception as e:
            # catch hipcc errors (or anything else) and record as failure
            gt_stats, gt_error, gt_file = None, str(e), None
            gt_compiles = False

        # ---- generated code ----
        try:
            gen_stats, gen_error, gen_file = executor.run(resp['generated'])
            gen_compiles = (gen_error is None)
        except Exception as e:
            gen_stats, gen_error, gen_file = None, str(e), None
            gen_compiles = False

        # record the result
        compilation_results.append({
            'ground_truth_compiles': gt_compiles,
            'generated_compiles':   gen_compiles,
            'ground_truth_time_us': (gt_stats or {}).get('kernel_duration_us'),
            'generated_time_us':    (gen_stats or {}).get('kernel_duration_us'),
            'ground_truth_error':   gt_error,
            'generated_error':      gen_error
        })

        # for the timing plot
        gt_time = (gt_stats or {}).get('kernel_duration_us')
        gen_time = (gen_stats or {}).get('kernel_duration_us')
        
        execution_times.append({'type': 'Ground Truth',
                                'time_us': gt_time,
                                'compiles': gt_compiles})
        execution_times.append({'type': 'Generated',
                                'time_us': gen_time,
                                'compiles': gen_compiles})

    return compilation_results, execution_times


def plot_execution_times(execution_times, output_dir):
    """Plot execution time comparison"""
    if pd is None:
        print("Warning: pandas not available, skipping execution time plots")
        return None
        
    df = pd.DataFrame(execution_times)
    
    # Filter out non-compiling kernels and None time values for time comparison
    df_compiling = df[(df['compiles'] == True) & (df['time_us'].notna())]
    
    if len(df_compiling) > 0:
        plt.figure(figsize=(12, 6))
        
        # Box plot of execution times
        plt.subplot(1, 2, 1)
        try:
            df_compiling.boxplot(column='time_us', by='type', ax=plt.gca())
        except:
            # Fallback to simple plot if boxplot fails
            for code_type in df_compiling['type'].unique():
                data = df_compiling[df_compiling['type'] == code_type]['time_us']
                plt.hist(data, alpha=0.5, label=code_type)
            plt.legend()
        plt.title('Execution Time Distribution')
        plt.ylabel('Time (microseconds)')
        plt.xlabel('Code Type')
        
        # Compilation success rate
        plt.subplot(1, 2, 2)
        success_rates = df.groupby('type')['compiles'].mean()
        success_rates.plot(kind='bar')
        plt.title('Compilation Success Rate')
        plt.ylabel('Success Rate')
        plt.xlabel('Code Type')
        plt.xticks(rotation=45)
        
        plt.tight_layout()
        plot_path = os.path.join(output_dir, "execution_analysis.png")
        plt.savefig(plot_path, dpi=300, bbox_inches='tight')
        plt.close()
        
        return plot_path
    
    return None

def run_evaluation(model, tokenizer, eval_file, output_dir, device="cuda:0"):
    """Run complete evaluation pipeline"""
    print("Loading evaluation dataset...")
    eval_data = load_eval_dataset(eval_file)
    
    print(f"Generating responses for {len(eval_data)} examples...")
    responses = generate_responses(model, tokenizer, eval_data, device)
    
    # Save responses
    if pd is not None:
        responses_df = pd.DataFrame(responses)
        responses_path = os.path.join(output_dir, "evaluation_responses.csv")
        responses_df.to_csv(responses_path, index=False)
    else:
        # Fallback to JSON if pandas not available
        responses_path = os.path.join(output_dir, "evaluation_responses.json")
        with open(responses_path, 'w') as f:
            json.dump(responses, f, indent=2)
    
    # Calculate fuzzy similarity
    print("Calculating fuzzy similarity...")
    similarities = []
    for response in responses:
        similarity = calculate_fuzzy_similarity(response['ground_truth'], response['generated'])
        similarities.append(similarity)
    avg_similarity = np.mean(similarities)
    
    # Get embeddings and plot comparison
    print("Getting code embeddings...")
    embedding_plot_path = None
    try:
        if get_embedder is not None:
            embedder = get_embedder()
            ground_truth_texts = [r['ground_truth'] for r in responses]
            generated_texts = [r['generated'] for r in responses]
            
            gt_embeddings = get_code_embeddings(ground_truth_texts, embedder)
            gen_embeddings = get_code_embeddings(generated_texts, embedder)
            
            embedding_plot_path = plot_embedding_comparison(gt_embeddings, gen_embeddings, output_dir)
        else:
            print("Embeddings module not available, skipping embedding analysis")
    except Exception as e:
        print(f"Error with embeddings: {e}")
        embedding_plot_path = None

    # Evaluate kernel compilation and execution
    print("Evaluating kernel compilation and execution...")
    if Executor is not None:
        executor = Executor(kernel_lang="hip")  # Assuming HIP kernels
        compilation_results, execution_times = evaluate_kernel_compilation(responses, executor)
        
        # Calculate compilation success rates
        gt_success_rate = np.mean([r['ground_truth_compiles'] for r in compilation_results])
        gen_success_rate = np.mean([r['generated_compiles'] for r in compilation_results])
        # Plot execution analysis
        execution_plot_path = plot_execution_times(execution_times, output_dir)
        
        # Calculate average execution times (filter out None values)
        gt_times = [r['ground_truth_time_us'] for r in compilation_results 
                   if r['ground_truth_compiles'] and r['ground_truth_time_us'] is not None]
        gen_times = [r['generated_time_us'] for r in compilation_results 
                    if r['generated_compiles'] and r['generated_time_us'] is not None]
        
        avg_gt_time = np.mean(gt_times) if gt_times else float('nan')
        avg_gen_time = np.mean(gen_times) if gen_times else float('nan')
    else:
        print("Executor not available, skipping compilation/execution analysis")
        gt_success_rate = float('nan')
        gen_success_rate = float('nan')
        avg_gt_time = float('nan')
        avg_gen_time = float('nan')
        execution_plot_path = None

    # Create summary metrics
    evaluation_metrics = {
        'total_examples': len(responses),
        'avg_fuzzy_similarity': avg_similarity,
        'ground_truth_compilation_success_rate': gt_success_rate,
        'generated_compilation_success_rate': gen_success_rate,
        'avg_ground_truth_time_us': avg_gt_time,
        'avg_generated_time_us': avg_gen_time
    }
    
    return evaluation_metrics, responses_path, embedding_plot_path, execution_plot_path

def load_logs(logfile):
    valid_events = {"sft_sample", "sft_sample_hpo", "sft_sample_phase1","sft_sample_optimization_phase1","sft_sample_optimization_hpo"}
    
    # Read all lines from the file
    with open(logfile) as fp:
        all_lines = [l.strip() for l in fp if l.strip()]
    
    # First, try to filter by valid events
    filtered_rows = []
    for line in all_lines:
        try:
            data = json.loads(line)
            # Check if this line contains any valid event or has the required fields
            if (any(event in line for event in valid_events) or 
                ('prompt' in data and 'response' in data) or 
                ('original_kernel' in data and 'optimized_kernel' in data)):
                filtered_rows.append(data)
        except json.JSONDecodeError:
            continue
    
    # If no rows found with event filtering, try to load all valid JSON lines
    if not filtered_rows:
        print("No rows found with event filtering, trying to load all valid JSON entries...")
        for line in all_lines:
            try:
                data = json.loads(line)
                if (('prompt' in data and 'response' in data) or 
                    ('original_kernel' in data and 'optimized_kernel' in data)):
                    filtered_rows.append(data)
            except json.JSONDecodeError:
                continue
    
    # Create normalized dataset with prompt/response format
    dataset_items = []
    for r in filtered_rows:
        if "prompt" in r and "response" in r:
            # Original format
            dataset_items.append({
                "prompt": r["prompt"], 
                "response": r["response"]
            })
        elif "original_kernel" in r and "optimized_kernel" in r:
            # Alternative format
            dataset_items.append({
                "prompt": r["original_kernel"], 
                "response": r["optimized_kernel"]
            })
        else:
            print(f"Warning: Skipping record with unknown format: {r.keys()}")
    
    print(f"Loaded {len(dataset_items)} training examples from {logfile}")
    
    if len(dataset_items) == 0:
        raise ValueError(f"No valid training data found in {logfile}. Please check the file format.")
    
    return Dataset.from_list(dataset_items)

def main(logfile, eval_file=None, experiment_name="LoRA-Finetuning", run_label=None):

    label_suffix = run_label or "default"
    OUTPUT_ROOT  = os.path.join("lora-finetuned", label_suffix)
    os.makedirs(OUTPUT_ROOT, exist_ok=True)  

    # Start MLflow run
    mlflow.set_experiment(experiment_name)
    
    with mlflow.start_run() as run:
        # Set run label/tag for easy comparison across runs
        if run_label:
            mlflow.set_tag("run_label", run_label)
            mlflow.set_tag("label", run_label)  # Alternative tag name for filtering
        
        # Log the input dataset information
        mlflow.log_param("dataset_source", logfile)
        if eval_file:
            mlflow.log_param("eval_dataset_source", eval_file)
        if run_label:
            mlflow.log_param("run_label", run_label)
        
        ds = load_logs(logfile)

        # Validate dataset size
        if len(ds) == 0:
            raise ValueError(f"No training data loaded from {logfile}. Please check the file format and content.")
        
        # Log dataset statistics
        mlflow.log_param("dataset_size", len(ds))
        
        # Sample and log a few examples
        sample_size = min(5, len(ds))
        sample_indices = random.sample(range(len(ds)), sample_size)
        sample_data = ds.select(sample_indices)
        
        # Log the dataset format
        mlflow.log_param("dataset_format", "prompt_response")
        
        for i, example in enumerate(sample_data):
            mlflow.log_text(
                f"Prompt: {example['prompt']}\nResponse: {example['response']}", 
                f"examples/example_{i}.txt"
            )

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
        
        # Log hyperparameters
        hyperparams = {
            "base_model": CFG["base_model"],
            "lora_r": CFG["r"],
            "lora_alpha": CFG["alpha"],
            "target_modules": str(CFG["target_modules"]),
            "lora_dropout": 0.1,
            "epochs": CFG["epochs"]
        }
        
        # Add run label to hyperparams for easier filtering
        if run_label:
            hyperparams["run_label"] = run_label
            
        mlflow.log_params(hyperparams)
        
        # Prepare model for k-bit training if using quantization
        model = prepare_model_for_kbit_training(model)
        model = get_peft_model(model, lora_cfg)

        # Define tokenization function
        def tokenize(batch):
            texts = []
            for p, r in zip(batch["prompt"], batch["response"]):
                # Ensure both prompt and response are strings and not None
                prompt_str = str(p) if p is not None else ""
                response_str = str(r) if r is not None else ""
                
                # Skip empty entries
                if not prompt_str.strip() or not response_str.strip():
                    continue
                    
                text = prompt_str + tok.bos_token + response_str + tok.eos_token
                texts.append(text)
            
            # If no valid texts, return empty tokenization
            if not texts:
                return {
                    'input_ids': [],
                    'attention_mask': []
                }
            
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
        
        # Debug: Check dataset size before tokenization
        print(f"Dataset size before tokenization: {len(ds)}")
        
        # Tokenize dataset
        ds = ds.map(tokenize, batched=True, remove_columns=["prompt", "response"])
        
        # Filter out any invalid entries (those with empty input_ids)
        def filter_valid_entries(example):
            return len(example.get('input_ids', [])) > 0
        
        ds = ds.filter(filter_valid_entries)
        
        # Debug: Check dataset size after tokenization
        print(f"Dataset size after tokenization: {len(ds)}")
        
        # Ensure we still have data after tokenization
        if len(ds) == 0:
            raise ValueError("Dataset became empty after tokenization. Check your data format.")
        
        # Set up training arguments
        training_args = TrainingArguments(
            output_dir=OUTPUT_ROOT,
            num_train_epochs=CFG["epochs"],
            per_device_train_batch_size=4,  
            gradient_accumulation_steps=4,
            learning_rate=5e-4,
            logging_steps=10,
            save_steps=500,
            save_total_limit=2,
            remove_unused_columns=False,
            dataloader_drop_last=False,  # Changed to False to preserve all data
            warmup_steps=min(100, len(ds) // 10),  # Adjust warmup steps based on dataset size
            weight_decay=0.01,
            fp16=True,  # Use mixed precision for memory efficiency
            report_to="none",  # We'll use MLflow directly instead
        )
        
        # Log more training parameters
        training_params = {
            "batch_size": 4,
            "gradient_accumulation_steps": 4,
            "learning_rate": 5e-4,
            "warmup_steps": min(100, len(ds) // 10),
            "weight_decay": 0.01
        }
        
        # Add run label to training params for easier comparison
        if run_label:
            training_params["run_label"] = run_label
            
        mlflow.log_params(training_params)
        
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
            callbacks=[TransformersMLflowCallback()],  
        )
        
        # Enable gradient checkpointing for memory efficiency
        model.gradient_checkpointing_enable()
        
        # Start training
        print("Starting training...")
        train_output = trainer.train()
        
        # Save the model
        trainer.save_model()
        tok.save_pretrained(OUTPUT_ROOT)
        
        # Log metrics from training
        mlflow.log_metrics({
            "training_loss": train_output.training_loss,
            "total_steps": trainer.state.global_step
        })

        log_history = trainer.state.log_history

        epochs = [entry["epoch"] for entry in log_history if "loss" in entry]
        losses = [entry["loss"] for entry in log_history if "loss" in entry]

        # Plot training loss
        plt.figure(figsize=(6,4))
        plt.plot(epochs, losses, marker="o")
        plt.xlabel("Epoch")
        plt.ylabel("Training Loss")
        plt.title("Loss Trend Across Epochs")
        plt.grid(True)

        # Save to disk and log to MLflow
        plot_path = os.path.join(training_args.output_dir, "loss_trend.png")
        plt.savefig(plot_path)
        mlflow.log_artifact(plot_path, "plots")
        plt.close()
        
        print(f"➡️ Saved loss plot to {plot_path}")
        
        # ==================== EVALUATION PHASE ====================
        if eval_file and os.path.exists(eval_file):
            print("\n" + "="*50)
            print("STARTING EVALUATION PHASE")
            print("="*50)
            
            # Create evaluation output directory
            eval_output_dir = os.path.join(training_args.output_dir, "evaluation")
            os.makedirs(eval_output_dir, exist_ok=True)
            
            try:
                # Load the fine-tuned model for evaluation
                print("Loading fine-tuned model for evaluation...")
                base_model_eval = AutoModelForCausalLM.from_pretrained(
                    CFG["base_model"], 
                    torch_dtype=torch.float16, 
                    device_map={"": "cuda:0"}
                )
                eval_model = PeftModel.from_pretrained(base_model_eval, training_args.output_dir)
                eval_model.eval()
 
                # Run evaluation
                evaluation_metrics, responses_path, embedding_plot_path, execution_plot_path = run_evaluation(
                    eval_model, tok, eval_file, eval_output_dir, device="cuda:0"
                )

                # Log evaluation metrics to MLflow
                eval_metrics_to_log = {
                    "eval_" + k: v for k, v in evaluation_metrics.items() 
                    if not np.isnan(v) and v is not None
                }
                
                # Add run label to evaluation metrics for easier comparison
                if run_label:
                    # Log metrics with run label prefix for easy filtering
                    eval_metrics_with_label = {
                        f"{run_label}_eval_{k}": v for k, v in evaluation_metrics.items() 
                        if not np.isnan(v) and v is not None
                    }
                    eval_metrics_to_log.update(eval_metrics_with_label)
                
                mlflow.log_metrics(eval_metrics_to_log)
                
                # Log evaluation artifacts
                mlflow.log_artifact(responses_path, "evaluation")
                if embedding_plot_path:
                    mlflow.log_artifact(embedding_plot_path, "plots")
                if execution_plot_path:
                    mlflow.log_artifact(execution_plot_path, "plots")
                
                # Print evaluation summary
                print("\n" + "="*50)
                print("EVALUATION RESULTS SUMMARY")
                print("="*50)
                print(f"Total examples evaluated: {evaluation_metrics['total_examples']}")
                print(f"Average fuzzy similarity: {evaluation_metrics['avg_fuzzy_similarity']:.3f}")
                print(f"Ground truth compilation success: {evaluation_metrics['ground_truth_compilation_success_rate']:.1%}")
                print(f"Generated code compilation success: {evaluation_metrics['generated_compilation_success_rate']:.1%}")
                
                if not np.isnan(evaluation_metrics['avg_ground_truth_time_us']):
                    print(f"Average ground truth execution time: {evaluation_metrics['avg_ground_truth_time_us']:.2f} μs")
                if not np.isnan(evaluation_metrics['avg_generated_time_us']):
                    print(f"Average generated code execution time: {evaluation_metrics['avg_generated_time_us']:.2f} μs")
                
                print(f"Evaluation artifacts saved to: {eval_output_dir}")
                print("="*50)
                
            except Exception as e:
                print(f"Error during evaluation: {e}")
                import traceback
                traceback.print_exc()
                mlflow.log_text(str(e), "evaluation_error.txt")
        
        # Log model to MLflow model registry
        model_name = f"lora-finetuned-{experiment_name}"
        if run_label:
            model_name += f"-{run_label}"
            
        mlflow.transformers.log_model(
            transformers_model={
                "model": model,
                "tokenizer": tok
            },
            name="model",
            task="text-generation",
            registered_model_name=model_name
        )
        
        # Print MLflow tracking information
        print(f"✅ MLflow run complete. View at: {MLFLOW_TRACKING_URI}/#/experiments/{run.info.experiment_id}/runs/{run.info.run_id}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--logfile", default="../dump/logsv0/combined_SFT_generation_history_train.jsonl", 
                    help="Training data JSONL file. Supports both prompt/response and original_kernel/optimized_kernel formats.")
    ap.add_argument("--eval-file", default=None, 
                    help="Evaluation dataset JSONL file. Supports both prompt/response and original_kernel/optimized_kernel formats.")
    ap.add_argument("--experiment-name", default="LoRA-Finetuning", 
                    help="MLflow experiment name")
    ap.add_argument("--run-label", default=None, 
                    help="Label/tag for this run to enable comparison across runs (e.g., 'baseline', 'optimized', 'v1.0')")
    args = ap.parse_args()
    main(args.logfile, args.eval_file, args.experiment_name, args.run_label)