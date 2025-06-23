

lang ?= hip

.PHONY: vector-store
vector-store:
	python -m src.data.build_vector_store --language "$(lang)"


dev-setup:
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
