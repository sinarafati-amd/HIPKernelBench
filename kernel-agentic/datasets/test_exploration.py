#!/usr/bin/env python3
"""
Test script for the dataset exploration function.
This script demonstrates how to use the explore_llm_dataset function.
"""

from explore_datasets import explore_llm_dataset

def test_dataset_exploration():
    """Test the dataset exploration function with different configurations."""
    
    print("🧪 Testing Dataset Exploration Function")
    print("=" * 50)
    
    # Test 1: Basic exploration
    print("\n📋 Test 1: Basic exploration")
    print("-" * 30)
    results = explore_llm_dataset(
        dataset_name="GPUMODE/kernelbot-data",
        config_name="leaderboards",
        max_samples=100,  # Small sample for quick testing
        show_examples=True,
        show_stats=True,
        show_plots=False  # Disable plots for testing
    )
    
    # Test 2: Minimal exploration
    print("\n📋 Test 2: Minimal exploration (no examples, no plots)")
    print("-" * 50)
    results_minimal = explore_llm_dataset(
        dataset_name="GPUMODE/kernelbot-data",
        config_name="leaderboards",
        max_samples=50,
        show_examples=False,
        show_stats=True,
        show_plots=False
    )
    
    # Test 3: Full exploration with plots
    print("\n📋 Test 3: Full exploration with plots")
    print("-" * 40)
    results_full = explore_llm_dataset(
        dataset_name="GPUMODE/kernelbot-data",
        config_name="leaderboards",
        max_samples=200,
        show_examples=True,
        show_stats=True,
        show_plots=True
    )
    
    print("\n✅ All tests completed!")
    print("\n📊 Summary of results:")
    print(f"- Test 1: {len(results.get('splits_info', {}))} splits analyzed")
    print(f"- Test 2: {len(results_minimal.get('splits_info', {}))} splits analyzed")
    print(f"- Test 3: {len(results_full.get('splits_info', {}))} splits analyzed")

if __name__ == "__main__":
    test_dataset_exploration() 