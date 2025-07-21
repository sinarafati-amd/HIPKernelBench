import os
from datasets import load_dataset, Dataset, DatasetDict
import pandas as pd
from typing import Dict, Any, Optional, Union
import json
from collections import Counter
import random
# Optional imports for visualization
import matplotlib.pyplot as plt
PLOTTING_AVAILABLE = True


def is_hashable(obj):
    try:
        hash(obj)
        return True
    except TypeError:
        return False


def explore_llm_dataset(dataset_name: str, 
                       config_name: Optional[str] = None,
                       max_samples: int = 1000,
                       show_examples: bool = True,
                       show_plots: bool = True):
    """
    Comprehensive function to explore an LLM dataset and extract useful information.
    
    Args:
        dataset_name: Name of the dataset (e.g., "GPUMODE/kernelbot-data")
        config_name: Configuration name if applicable (e.g., "leaderboards")
        max_samples: Maximum number of samples to analyze for statistics
        show_examples: Whether to print example samples
        show_plots: Whether to create visualizations
    
    """

    # Create a directory for the results
    folder_name = f"kernel-agentic/datasets/{dataset_name.replace('/', '_')}"
    os.makedirs(folder_name, exist_ok=True)
    
    print(f"🔍 Exploring dataset: {dataset_name}")
    if config_name:
        print(f"📋 Configuration: {config_name}")
    print("=" * 60)
    
    # Load the dataset
    try:
        if config_name:
            ds = load_dataset(dataset_name, config_name)
        else:
            ds = load_dataset(dataset_name)
    except Exception as e:
        print(f"❌ Error loading dataset: {e}")
    
    results = {
        "dataset_name": dataset_name,
        "config_name": config_name,
        "dataset_info": {},
        "splits_info": {},
        "features_info": {},
        "sample_data": {},
        "statistics": {},
        "examples": {}
    }
    
    # Basic dataset information
    print("\n📊 DATASET OVERVIEW")
    print("-" * 30)
    

    # Splits information - handle both DatasetDict and individual Dataset
    print(f"\n📈 DATASET SPLITS")
    print("-" * 20)
    
    if isinstance(ds, DatasetDict):
        splits = ds
    elif isinstance(ds, Dataset):
        splits = {"default": ds}
    else:
        # Fallback for other types
        try:
            splits = ds if hasattr(ds, 'keys') else {"default": ds}
        except:
            splits = {"default": ds}
    
    for split_name, split_data in splits.items():
        split_info = {
            "num_examples": len(split_data),
            "features": list(split_data.features.keys()) if hasattr(split_data, 'features') else [],
            "column_names": split_data.column_names if hasattr(split_data, 'column_names') else []
        }
        results["splits_info"][split_name] = split_info
        
        print(f"Split: {split_name}")
        print(f"  - Number of examples: {split_info['num_examples']:,}")
        print(f"  - Features: {split_info['features']}")
        print(f"  - Columns: {split_info['column_names']}")
    

    # Sample data analysis
    print(f"\n📝 SAMPLE DATA ANALYSIS")
    print("-" * 25)
    
    for split_name, split_data in splits.items():
        print(f"\nAnalyzing split: {split_name}")
        
        # Get sample data
        sample_size = min(max_samples, len(split_data))
        # Random sampling instead of sequential
        random_indices = random.sample(range(len(split_data)), sample_size)
        sample_data = split_data.select(random_indices)
        
        # Convert to pandas for easier analysis
        df = sample_data.to_pandas()
        results["sample_data"][split_name] = {
            "shape": df.shape,
            "columns": list(df.columns),
            "dtypes": df.dtypes.to_dict(),
            "null_counts": df.isnull().sum().to_dict()
        }
        
        print(f"  - Sample shape: {df.shape}")
        print(f"  - Columns: {list(df.columns)}")
        print(f"  - Data types: {dict(df.dtypes)}")
        print(f"  - Null values: {dict(df.isnull().sum())}")
        
        # Show examples
        if show_examples:
            print(f"\n  📋 EXAMPLES FROM {split_name.upper()}:")
            print("  " + "-" * 40)
            for i in range(min(3, len(df))):
                print(f"  Example {i+1}:")
                for col in df.columns:
                    value = df.iloc[i][col]
                    if isinstance(value, str) and len(value) > 1000:
                        value = value[:1000] + "..."
                    print(f"    {col}: {value}")
                print()
            
            results["examples"][split_name] = df.head(3).to_dict('records')
        
        # Statistical analysis
        print(f"\n  📊 STATISTICS FOR {split_name.upper()}:")
        print("  " + "-" * 40)
        
        stats = {}
        
        # Text length statistics for string columns
        text_columns = df.select_dtypes(include=['object']).columns
        for col in text_columns:
            if df[col].dtype == 'object':
                text_lengths = df[col].astype(str).str.len()
                stats[col] = {
                    "mean_length": text_lengths.mean(),
                    "median_length": text_lengths.median(),
                    "min_length": text_lengths.min(),
                    "max_length": text_lengths.max(),
                    "std_length": text_lengths.std()
                }
                print(f"    {col} (text length):")
                print(f"      - Mean: {stats[col]['mean_length']:.1f}")
                print(f"      - Median: {stats[col]['median_length']:.1f}")
                print(f"      - Min: {stats[col]['min_length']}")
                print(f"      - Max: {stats[col]['max_length']}")
                print(f"      - Std: {stats[col]['std_length']:.1f}")
        
        
        # Create visualizations
        if show_plots and PLOTTING_AVAILABLE:
            print(f"\n  📈 CREATING VISUALIZATIONS FOR {split_name.upper()}")
            print("  " + "-" * 45)
            
            # Text length distribution
            for col in text_columns:
                if df[col].dtype == 'object':
                    text_lengths = df[col].astype(str).str.len()
                    
                    plt.figure(figsize=(10, 6))
                    plt.hist(text_lengths, bins=50, alpha=0.7, edgecolor='black')
                    plt.title(f'Text Length Distribution - {col} ({split_name})')
                    plt.xlabel('Text Length')
                    plt.ylabel('Frequency')
                    plt.grid(True, alpha=0.3)
                    plt.tight_layout()
                    plt.savefig(f'{folder_name}/{split_name}_{col}_length_dist.png', dpi=300, bbox_inches='tight')
                    plt.close()
                    print(f"    - Saved: {split_name}_{col}_length_dist.png")
            
            # Value counts for categorical columns
            for col in df.columns:
                if is_hashable(df[col]) and df[col].nunique() < 32:  # Only plot if not too many categories
                    try:
                        value_counts = df[col].value_counts()
                        
                        # Check if values are plottable (not numpy arrays or complex types)
                        plottable = True
                        for val in value_counts.index:
                            if isinstance(val, (list, dict, tuple)) or hasattr(val, '__array__'):
                                plottable = False
                                break
                        
                        if plottable:
                            plt.figure(figsize=(10, 6))
                            value_counts.plot(kind='bar')
                            plt.title(f'Value Counts - {col} ({split_name})')
                            plt.xlabel(col)
                            plt.ylabel('Count')
                            plt.xticks(rotation=45)
                            plt.tight_layout()
                            plt.savefig(f'{folder_name}/{split_name}_{col}_value_counts.png', dpi=300, bbox_inches='tight')
                            plt.close()
                            print(f"    - Saved: {split_name}_{col}_value_counts.png")
                        else:
                            print(f"    - Skipped plotting {col} (contains non-plottable values)")
                    except Exception as e:
                        print(f"    - Error plotting {col}: {e}")
            
        elif show_plots and not PLOTTING_AVAILABLE:
            print("    - Skipping plots (matplotlib/seaborn not available)")
    
    # Summary
    print(f"\n🎯 EXPLORATION SUMMARY")
    print("=" * 30)
    print(f"Dataset: {dataset_name}")
    if config_name:
        print(f"Config: {config_name}")
    print(f"Total splits: {len(splits)}")
    for split_name, split_info in results["splits_info"].items():
        print(f"  - {split_name}: {split_info['num_examples']:,} examples")
    
    print(f"\n✅ Exploration complete! Check the generated plots and results.")
    
    # Save results to JSON for later reference
    with open(f'{folder_name}/dataset_exploration_results.json', 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n💾 Results saved to '{folder_name}/dataset_exploration_results.json'")

# Example usage
if __name__ == "__main__":
    # Explore the kernelbot dataset

    dataset_dict = {
        "kernelbook": "GPUMODE/KernelBook", # config_name: default
        "categorized_triton_data_permissive": "GPUMODE/categorized_triton_data_permissive", # config_name: default
        "ai_cuda_engineer_archive": "SakanaAI/AI-CUDA-Engineer-Archive", # config_name: default
        "kernelbot_data": "GPUMODE/kernelbot-data", # config_name: leaderboards, submissions,  successful_submissions
        "nvidia_compute_eval": "nvidia/compute-eval" # No access
    }
    
    explore_llm_dataset(
        dataset_name=dataset_dict["categorized_triton_data_permissive"],
        config_name="default",
        max_samples=500,  # Analyze up to 500 samples for statistics
        show_examples=True,
        show_plots=True
    )
    