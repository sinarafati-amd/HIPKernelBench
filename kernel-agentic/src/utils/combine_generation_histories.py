import argparse
import os
import json

def combine_generation_histories(base_path):
    combined_entries = []
    combined_entries_total=[]
    # Iterate through all items in the base directory
    for item in os.listdir(base_path):
        subfolder_path = os.path.join(base_path, item)
        if os.path.isdir(subfolder_path):
            jsonl_path = os.path.join(subfolder_path, "generation_history.jsonl")
            if os.path.isfile(jsonl_path):
                with open(jsonl_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            # Keep only entries with "event": "sft_sample"
                            combined_entries_total.append(entry)
                            if entry.get("event") == "sft_sample":
                                combined_entries.append(entry)
                        except json.JSONDecodeError as e:
                            print(f"Skipping invalid JSON in {jsonl_path}: {e}")

    # Save filtered entries to a single file in the base path
    output_path = os.path.join(base_path, "combined_SFT_generation_history.jsonl")
    with open(output_path, 'w', encoding='utf-8') as out_f:
        for entry in combined_entries:
            out_f.write(json.dumps(entry) + '\n')

    print(f"Combined {len(combined_entries)} 'sft_sample' entries into: {output_path}")

    output_path = os.path.join(base_path, "combined_generation_history.jsonl")
    with open(output_path, 'w', encoding='utf-8') as out_f:
        for entry in combined_entries_total:
            out_f.write(json.dumps(entry) + '\n')

    print(f"Combined {len(combined_entries_total)} entries into: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Combine generation_history.jsonl files from subfolders.")
    parser.add_argument("--folder_path", type=str, help="Path to the main folder containing subfolders with generation_history.jsonl files.")
    
    args = parser.parse_args()
    combine_generation_histories(args.folder_path)

if __name__ == "__main__":
    main()
