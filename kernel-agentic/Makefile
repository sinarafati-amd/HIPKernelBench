

LANG ?= hip
TORCH_FILE_DIR ?= torch_codes
MODE ?= multimodal

dev-setup:
	uv pip install --index-url https://download.pytorch.org/whl/rocm6.3 -r pytorch_rocm.txt --no-cache-dir && \

.PHONY: vector-store
vector-store:
	@echo "Building vector store for lang='$(LANG)' mode='$(MODE)'..."
	python -m src.data.build_vector_store \
	       --language "$(LANG)" \
	       --mode "$(MODE)"

install_rocm_pytorch:
	uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/rocm6.3

dev_setup:
	uv pip install -r requirements.txt


run:
	@mkdir -p logs
	@for file in $(TORCH_FILE_DIR)/*.py; do \
		base=$${file##*/}; \
		echo "Running $$file"; \
		name=$${base%.py}; \
		python -m src.agents.orchestrator --torch-file $$file; \
		mkdir -p logs/$$name; \
		mv logs/generation_history.jsonl logs/$$name/; \
	done

train-lora:
	python -m src.training.lora_finetune --logfile logs/combined_SFT_generation_history.jsonl

combine-history:
	python src/utils/combine_generation_histories.py --folder_path logs
	
baseline-time:
	python scripts/generate_baseline_time.py TORCH_FILE=$(TORCH_FILE)

run-check:
	python scripts/run_and_check.py HIP_BIN=$(BIN) TORCH_FILE=$(TORCH_FILE)

analyse-fastp:
	python scripts/fastp_analysis.py RUN_DIR=$(RUN)

grpo-finetune:
	python -m src.training.grpo_finetune --logfile=logs/combined_generation_history.jsonl
	
omniwise-server:
	python -m src.omniwise.resources.python.inference_server

.PHONY: download_nltk
download_nltk:
	python -m nltk.downloader punkt punkt_tab
