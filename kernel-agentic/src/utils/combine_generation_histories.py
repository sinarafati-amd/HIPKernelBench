import argparse
import os
import json
import argparse
import os
import json

def combine_generation_histories(base_path):
    combined_entries = []
    combined_entries_total = {}  # Changed to dict for nested structure
    
    # Iterate through all items in the base directory
    for item in os.listdir(base_path):
        subfolder_path = os.path.join(base_path, item)
        if os.path.isdir(subfolder_path):
            jsonl_path = os.path.join(subfolder_path, "generation_history.jsonl")
            if os.path.isfile(jsonl_path):
                best_sft_entry = None
                smallest_hip_us = None
                subfolder_entries = []  # Store all entries for this subfolder
                
                with open(jsonl_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            subfolder_entries.append(entry)
                            
                            # Check if this is an sft_ sample event
                            event = entry.get("event", "")
                            if event in ["sft_sample_phase1", "sft_sample_hpo"]:
                                # Get hip_us for comparison
                                current_hip_us = entry.get("hip_us")
                                
                                if current_hip_us is not None:
                                    if best_sft_entry is None or current_hip_us < smallest_hip_us:
                                        best_sft_entry = entry
                                        smallest_hip_us = current_hip_us
                                        
                        except json.JSONDecodeError as e:
                            print(f"Skipping invalid JSON in {jsonl_path}: {e}")
                
                # Add the best sft_ entry from this subfolder if found
                if best_sft_entry is not None:
                    combined_entries.append(best_sft_entry)
                
                # Add all entries from this subfolder to the nested structure
                combined_entries_total[item] = subfolder_entries

    # Save filtered entries to a single file in the base path
    output_path = os.path.join(base_path, "combined_SFT_generation_history.jsonl")
    with open(output_path, 'w', encoding='utf-8') as out_f:
        for entry in combined_entries:
            out_f.write(json.dumps(entry) + '\n')

    print(f"Combined {len(combined_entries)} best 'sft_sample_*' entries (smallest hip_us) into: {output_path}")

    # Save nested structure as JSON
    output_path = os.path.join(base_path, "combined_generation_history.json")
    with open(output_path, 'w', encoding='utf-8') as out_f:
        json.dump(combined_entries_total, out_f, indent=2)

    total_entries = sum(len(entries) for entries in combined_entries_total.values())
    print(f"Combined {total_entries} total entries from {len(combined_entries_total)} folders into: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Combine generation_history.jsonl files from subfolders.")
    parser.add_argument("--folder_path", type=str, help="Path to the main folder containing subfolders with generation_history.jsonl files.")
    
    args = parser.parse_args()
    combine_generation_histories(args.folder_path)

if __name__ == "__main__":
    main()