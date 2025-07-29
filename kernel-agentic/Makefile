
TORCH_FILE_DIR ?= torch_codes
KERNEL_FILE_DIR ?= kernel_codes
KERNEL_LANG ?= hip
MODE ?= multimodal

dev-setup:
	bash scripts/dev_setup.sh

.PHONY: vector-store
vector-store:
	@echo "Building vector store for KERNEL_LANG='$(KERNEL_LANG)' MODE='$(MODE)'..."
	python -m nltk.downloader punkt punkt_tab 
	python -m src.data.build_vector_store \
	       --language "$(KERNEL_LANG)" \
	       --mode "$(MODE)"

run:
	@mkdir -p logs
	@for file in $(TORCH_FILE_DIR)/*.py; do \
		base=$${file##*/}; \
		echo "Running $$file"; \
		name=$${base%.py}; \
		python -m src.agents.orchestrator --mode pytorch2kernel --torch-file $$file; \
		mkdir -p logs/$$name; \
		mv logs/generation_history.jsonl logs/$$name/; \
	done


optimize-kernels:
	@mkdir -p logs
	@if [ "$(KERNEL_FILE)" ]; then \
		echo "Optimizing single kernel file: $(KERNEL_FILE)"; \
		python -m src.agents.orchestrator --mode kernel2kernel --kernel-file "$(KERNEL_FILE)"; \
	else \
		echo "Optimizing all kernel files in $(KERNEL_FILE_DIR)"; \
		for file in $(KERNEL_FILE_DIR)/*.hip $(KERNEL_FILE_DIR)/*.cu $(KERNEL_FILE_DIR)/*.py; do \
			if [ -f "$$file" ]; then \
				base=$${file##*/}; \
				echo "Optimizing $$file"; \
				name=$${base%.*}; \
				python -m src.agents.orchestrator --mode kernel2kernel --kernel-file $$file; \
				mkdir -p logs/$${name}_opt; \
				if [ -f logs/generation_history.jsonl ]; then \
					mv logs/generation_history.jsonl logs/$${name}_opt/; \
				fi; \
			fi; \
		done; \
	fi

train-lora:
	@if [ -z "$(LOGFILE)" ]; then \
		echo "Using default logfile: logs/combined_SFT_generation_history.jsonl"; \
		LOGFILE="logs/combined_SFT_generation_history.jsonl"; \
	fi; \
	python -m src.training.lora_finetune \
		--logfile "$(LOGFILE)" \
		$(if $(EVAL_FILE),--eval-file "$(EVAL_FILE)") \
		$(if $(EXPERIMENT_NAME),--experiment-name "$(EXPERIMENT_NAME)") \
		$(if $(RUN_LABEL),--run-label "$(RUN_LABEL)")

combine-history:
	@if [ -z "$(FOLDER_PATH)" ]; then \
		echo "Using default folder path: logs"; \
		python src/utils/combine_generation_histories.py --folder_path logs; \
	else \
		echo "Using folder path: $(FOLDER_PATH)"; \
		python src/utils/combine_generation_histories.py --folder_path $(FOLDER_PATH); \
	fi
	
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

post-process:
	@if [ -z "$(INPUT_DIR)" ] || [ -z "$(RUN_NAME)" ]; then \
		echo "Please provide INPUT_DIR and RUN_NAME variables."; \
		echo "Example: make post-process INPUT_DIR=logs RUN_NAME=my_run"; \
		exit 1; \
	fi; \
	echo "Running post-processing script with input=$(INPUT_DIR) and run-name=$(RUN_NAME)..."; \
	python src/utils/post_process.py --input "$(INPUT_DIR)" --run-name "$(RUN_NAME)"
