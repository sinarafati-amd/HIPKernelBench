dev-setup:
	pip install -r requirements.txt

vector-store:
	python -m src.data.build_vector_store --pdf docs/hip_spec.pdf

run:
	@for file in $(TORCH_FILE_DIR)/*.py; do \
		base=$${file##*/}; \
		echo "Running $$file"; \
		name=$${base%.py}; \
		python -m src.agents.orchestrator --torch-file $$file --iters $(ITERS); \
		mv logs/generation_history.jsonl logs/$$name/generation_history.jsonl; \
	done

train-lora:
	python -m src.training.lora_finetune --logfile logs/generation_history.jsonl


baseline-time:
	python scripts/generate_baseline_time.py TORCH_FILE=$(TORCH_FILE)

run-check:
	python scripts/run_and_check.py HIP_BIN=$(BIN) TORCH_FILE=$(TORCH_FILE)

analyse-fastp:
	python scripts/fastp_analysis.py RUN_DIR=$(RUN)

grpo-finetune:
	python -m src.training.grpo_finetune --logfile=logs/generation_history.jsonl