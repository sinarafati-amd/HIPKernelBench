import argparse
import os
import json
import random

def combine_generation_histories(base_path, val_ratio, seed):
    combined_sft = []
    full_history = {}

    # 1) Load and pick best SFT entry per folder; also gather full entries.
    for item in os.listdir(base_path):
        subfolder = os.path.join(base_path, item)
        if not os.path.isdir(subfolder):
            continue

        history_file = os.path.join(subfolder, "generation_history.jsonl")
        if not os.path.isfile(history_file):
            continue

        best_entry = None
        best_hip_us = None
        all_entries = []

        with open(history_file, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                all_entries.append(entry)

                ev = entry.get("event", "")
                if ev in ("sft_sample_phase1", "sft_sample_hpo"):
                    hip_us = entry.get("hip_us")
                    if hip_us is not None and (best_entry is None or hip_us < best_hip_us):
                        best_entry = entry
                        best_hip_us = hip_us

        if best_entry:
            combined_sft.append(best_entry)
        full_history[item] = all_entries

    # 2) Shuffle + split SFT entries
    random.seed(seed)
    indices = list(range(len(combined_sft)))
    random.shuffle(indices)

    cutoff = int(len(indices) * (1 - val_ratio))
    train_idx, eval_idx = indices[:cutoff], indices[cutoff:]

    sft_train = [combined_sft[i] for i in train_idx]
    sft_eval  = [combined_sft[i] for i in eval_idx]

    # 3) Write SFT train/eval JSONL
    out_train_sft = os.path.join(base_path, "combined_SFT_generation_history_train.jsonl")
    out_eval_sft  = os.path.join(base_path, "combined_SFT_generation_history_eval.jsonl")

    with open(out_train_sft, 'w', encoding='utf-8') as f:
        for e in sft_train:
            f.write(json.dumps(e) + "\n")
    with open(out_eval_sft, 'w', encoding='utf-8') as f:
        for e in sft_eval:
            f.write(json.dumps(e) + "\n")

    print(f"Wrote {len(sft_train)} SFT train entries → {out_train_sft}")
    print(f"Wrote {len(sft_eval)}  SFT eval entries  → {out_eval_sft}")

    # 4) Flatten full history, shuffle + split
    all_pairs = []
    for folder, entries in full_history.items():
        for e in entries:
            all_pairs.append((folder, e))

    random.shuffle(all_pairs)
    cutoff = int(len(all_pairs) * (1 - val_ratio))
    train_pairs = all_pairs[:cutoff]
    eval_pairs  = all_pairs[cutoff:]

    # 5) Re-nest by folder
    train_hist = {}
    eval_hist  = {}
    for folder, e in train_pairs:
        train_hist.setdefault(folder, []).append(e)
    for folder, e in eval_pairs:
        eval_hist.setdefault(folder, []).append(e)

    # 6) Write nested JSONs
    out_train_full = os.path.join(base_path, "combined_generation_history_train.json")
    out_eval_full  = os.path.join(base_path, "combined_generation_history_eval.json")

    with open(out_train_full, 'w', encoding='utf-8') as f:
        json.dump(train_hist, f, indent=2)
    with open(out_eval_full, 'w', encoding='utf-8') as f:
        json.dump(eval_hist, f, indent=2)

    print(f"Wrote {sum(len(v) for v in train_hist.values())} total train entries → {out_train_full}")
    print(f"Wrote {sum(len(v) for v in eval_hist.values())} total eval entries  → {out_eval_full}")

def main():
    parser = argparse.ArgumentParser(
        description="Combine and split generation_history.jsonl files from subfolders."
    )
    parser.add_argument(
        "--folder_path", type=str, required=True,
        help="Path to main folder containing subfolders with generation_history.jsonl"
    )
    parser.add_argument(
        "--validation_ratio", type=float, default=0.1,
        help="Fraction of data to use for evaluation (default: 0.2)"
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for reproducible splitting (default: 42)"
    )

    args = parser.parse_args()
    combine_generation_histories(args.folder_path, args.validation_ratio, args.seed)

if __name__ == "__main__":
    main()
