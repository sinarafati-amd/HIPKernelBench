import sys
import os
import argparse 
from pathlib import Path
from argparse import ArgumentParser
import json
import seaborn as sns
import matplotlib.pyplot as plt
import numpy as np
import matplotlib.gridspec as gridspec
from scipy.stats import gaussian_kde
import pandas as pd
import yaml
from datetime import datetime
import re
from typing import Dict, List, Any
import shutil

def load_config(config_path: str = "config.yml") -> Dict[str, Any]:
    """Load configuration from YAML file."""
    try:
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Warning: {config_path} not found, using empty config")
        return {}

def load_grouping_config(grouping_path: str = "grouping.yml") -> Dict[str, Any]:
    """Load grouping configuration from YAML file."""
    try:
        with open(grouping_path, 'r') as f:
            config = yaml.safe_load(f)
            print(f"✅ Loaded grouping configuration from: {grouping_path}")
            return config
    except FileNotFoundError:
        print(f"❌ Error: Grouping file not found: {grouping_path}")
        print(f"   Please ensure the grouping.yml file exists or specify a different path with --grouping")
        return {}
    except yaml.YAMLError as e:
        print(f"❌ Error parsing YAML file {grouping_path}: {e}")
        return {}

def extract_date_from_folder(folder_name: str) -> str:
    """Extract date from folder name using various patterns."""
    # Try different patterns
    patterns = [
        r'logs-level\d+-v(\d+)',  
        r'logs-(\d{4}-\d{2}-\d{2})',  
        r'logs-(\d{8})',  
        r'(\d{4}-\d{2}-\d{2})',  
        r'(\d{8})',  
    ]
    
    for pattern in patterns:
        match = re.search(pattern, folder_name)
        if match:
            return match.group(1)
    
    # If no date pattern found, use the folder name as is
    return folder_name.replace('logs-', '').replace('/', '')

def calculate_overall_speedup(baseline_times: List[float], hip_times: List[float]) -> float:
    """Calculate overall speedup as harmonic mean of individual speedups."""
    if not baseline_times or not hip_times:
        return 0.0
    
    speedups = []
    for bt, ht in zip(baseline_times, hip_times):
        if ht > 0:
            speedups.append(bt / ht)
    
    if not speedups:
        return 0.0
    
    # Harmonic mean of speedups
    return len(speedups) / sum(1/s for s in speedups)

def generate_summary_stats(baseline_times: List[float], hip_times: List[float]) -> Dict[str, float]:
    """Generate summary statistics for a run."""
    if not baseline_times or not hip_times:
        return {
            'total_kernels': 0,
            'avg_torch_time': 0.0,
            'avg_hip_time': 0.0,
            'overall_speedup': 0.0,
            'success_rate': 0.0
        }
    
    successful_pairs = [(bt, ht) for bt, ht in zip(baseline_times, hip_times) if ht > 0]
    
    return {
        'total_kernels': len(baseline_times),
        'successful_kernels': len(successful_pairs),
        'avg_torch_time': np.mean(baseline_times),
        'avg_hip_time': np.mean([ht for _, ht in successful_pairs]) if successful_pairs else 0.0,
        'overall_speedup': calculate_overall_speedup(baseline_times, hip_times),
        'success_rate': len(successful_pairs) / len(baseline_times) * 100 if baseline_times else 0.0
    }

def copy_plots_to_reports(run_name: str, input_folder: str, main_folder: str, config: Dict[str, Any] = None) -> Dict[str, str]:
    """Copy plot images and prompts to reports folder and return the new paths."""
    reports_folder = os.path.join(main_folder, "reports")
    run_reports_folder = os.path.join(reports_folder, run_name)
    
    # Create reports directory structure
    os.makedirs(run_reports_folder, exist_ok=True)
    
    # Initialize copied_paths at the beginning
    copied_paths = {}
    
    # Detect mode to determine which plots to copy
    run_mode = detect_run_mode(input_folder)
    
    if run_mode == 'kernel2kernel':
        # Copy kernel optimization plots
        plot_files = [
            'kernel_optimization_speedup_distribution.png',
            'kernel_optimization_comparison.png', 
            'kernel_optimization_summary.png'
        ]
        
        # Copy kernel optimization report
        report_file = 'kernel_optimization_report.md'
        src_report = os.path.join(input_folder, report_file)
        if os.path.exists(src_report):
            dst_report = os.path.join(run_reports_folder, report_file)
            shutil.copy2(src_report, dst_report)
            copied_paths[report_file] = dst_report
            print(f"📋 Copied {report_file} to reports folder")
    else:
        # Copy pytorch2kernel plots
        plot_files = [
            'average_baseline_barplot_grouped_log.png',
            'baseline_density_cleaned.png'
        ]
    
    # Copy plot files
    for plot_file in plot_files:
        src_path = os.path.join(input_folder, plot_file)
        if os.path.exists(src_path):
            dst_path = os.path.join(run_reports_folder, plot_file)
            shutil.copy2(src_path, dst_path)
            copied_paths[plot_file] = dst_path
            print(f"📋 Copied {plot_file} to reports folder")
    
    # Extract kernel language from config
    kernel_lang = None
    if config and 'Pipeline' in config:
        kernel_lang = config['Pipeline'].get('kernel_lang')
    
    # Copy prompts folder with language filtering
    prompts_copied = copy_prompts_to_reports(run_name, main_folder, kernel_lang)
    if prompts_copied:
        copied_paths['prompts'] = prompts_copied
    
    return copied_paths

def copy_prompts_to_reports(run_name: str, main_folder: str, kernel_lang: str = None) -> Dict[str, str]:
    """Copy prompts folder to reports for the specific run, filtered by language if specified."""
    # Find the prompts folder - try multiple possible locations
    possible_prompts_paths = [
        os.path.join(main_folder, "kernel-agentic", "src", "prompts"),
        os.path.join(main_folder, "src", "prompts"),
        os.path.join(os.path.dirname(main_folder), "kernel-agentic", "src", "prompts"),
        "src/prompts"  # Relative path fallback
    ]
    
    prompts_src_folder = None
    for path in possible_prompts_paths:
        if os.path.exists(path):
            prompts_src_folder = path
            break
    
    if not prompts_src_folder:
        print("⚠️  Prompts folder not found, skipping prompt copying")
        return {}
    
    # Create prompts destination
    reports_folder = os.path.join(main_folder, "reports")
    run_reports_folder = os.path.join(reports_folder, run_name)
    prompts_dst_folder = os.path.join(run_reports_folder, "prompts")
    
    os.makedirs(prompts_dst_folder, exist_ok=True)
    
    copied_prompts = {}
    
    # Copy prompt files - filter by language if specified
    for filename in os.listdir(prompts_src_folder):
        if filename.endswith(('.txt', '.md', '.prompt')):
            # If kernel_lang is specified, only copy prompts that match the language
            if kernel_lang:
                if kernel_lang.lower() in filename.lower() or 'general' in filename.lower() or not any(lang in filename.lower() for lang in ['hip', 'cuda', 'triton']):
                    src_file = os.path.join(prompts_src_folder, filename)
                    dst_file = os.path.join(prompts_dst_folder, filename)
                    shutil.copy2(src_file, dst_file)
                    copied_prompts[filename] = dst_file
                    print(f"Copied prompt: {filename} (language: {kernel_lang})")
            else:
                # No language filter, copy all prompts
                src_file = os.path.join(prompts_src_folder, filename)
                dst_file = os.path.join(prompts_dst_folder, filename)
                shutil.copy2(src_file, dst_file)
                copied_prompts[filename] = dst_file
                print(f"Copied prompt: {filename}")
    
    # Copy cheat sheets if they exist and match the language
    copy_cheat_sheets_to_reports(run_reports_folder, main_folder, kernel_lang)
    
    # Create a summary of prompts used
    create_prompts_summary(prompts_dst_folder, copied_prompts)
    
    return copied_prompts

def copy_cheat_sheets_to_reports(run_reports_folder: str, main_folder: str, kernel_lang: str = None) -> None:
    """Copy cheat sheets to reports folder, filtered by language if specified."""
    # Find cheat sheets folder - they are JSON files in kernel-agentic/src/sheets
    possible_cheat_paths = [
        os.path.join(main_folder, "kernel-agentic", "src", "sheets"),
        os.path.join(main_folder, "src", "sheets"),
        os.path.join(os.path.dirname(main_folder), "kernel-agentic", "src", "sheets"),
        "src/sheets"  # Relative path fallback
    ]
    
    cheat_src_folder = None
    for path in possible_cheat_paths:
        if os.path.exists(path):
            cheat_src_folder = path
            break
    
    if not cheat_src_folder:
        print("⚠️  Cheat sheets folder not found, skipping cheat sheet copying")
        return
    
    # Create cheat sheets destination
    cheat_dst_folder = os.path.join(run_reports_folder, "cheat_sheets")
    os.makedirs(cheat_dst_folder, exist_ok=True)
    
    # Copy cheat sheets based on language - they are JSON files
    if kernel_lang and kernel_lang.lower() in ['hip', 'cuda', 'triton']:
        # Look for JSON files that match the language
        for filename in os.listdir(cheat_src_folder):
            if filename.endswith('.json') and kernel_lang.lower() in filename.lower():
                src_file = os.path.join(cheat_src_folder, filename)
                dst_file = os.path.join(cheat_dst_folder, filename)
                shutil.copy2(src_file, dst_file)
                print(f"📋 Copied cheat sheet: {filename} (language: {kernel_lang})")
        
        # Also copy any general cheat sheets (that don't have specific language in name)
        for filename in os.listdir(cheat_src_folder):
            if filename.endswith('.json') and not any(lang in filename.lower() for lang in ['hip', 'cuda', 'triton']):
                src_file = os.path.join(cheat_src_folder, filename)
                dst_file = os.path.join(cheat_dst_folder, filename)
                shutil.copy2(src_file, dst_file)
                print(f"📋 Copied general cheat sheet: {filename}")
    else:
        print("⚠️  No specific language specified, skipping cheat sheet copying")

def create_prompts_summary(prompts_folder: str, copied_prompts: Dict[str, str]) -> None:
    """Create a summary markdown file of all prompts used in this run."""
    summary_path = os.path.join(prompts_folder, "PROMPTS_SUMMARY.md")
    
    content = f"""# Prompts Used in This Run

*Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*

This folder contains all the prompts that were used during this optimization run. These prompts define how the AI models analyze PyTorch code and generate optimized kernels.

## 📝 Prompt Files

"""
    
    # Categorize prompts by type
    categories = {
        'naive': [],
        'opt': [],
        'error': [],
        'refinement': [],
        'other': []
    }
    
    for filename in sorted(copied_prompts.keys()):
        if 'naive' in filename.lower():
            categories['naive'].append(filename)
        elif 'opt' in filename.lower():
            categories['opt'].append(filename)
        elif 'error' in filename.lower():
            categories['error'].append(filename)
        elif 'refin' in filename.lower():
            categories['refinement'].append(filename)
        else:
            categories['other'].append(filename)
    
    # Add categorized prompts to summary
    if categories['naive']:
        content += "### 🌱 Naive Generation Prompts\n"
        content += "These prompts are used for initial kernel generation:\n"
        for filename in categories['naive']:
            content += f"- [`{filename}`](./{filename})\n"
        content += "\n"
    
    if categories['opt']:
        content += "### ⚡ Optimization Prompts\n"
        content += "These prompts are used for performance optimization:\n"
        for filename in categories['opt']:
            content += f"- [`{filename}`](./{filename})\n"
        content += "\n"
    
    if categories['error']:
        content += "### 🔧 Error Handling Prompts\n"
        content += "These prompts are used for fixing compilation/runtime errors:\n"
        for filename in categories['error']:
            content += f"- [`{filename}`](./{filename})\n"
        content += "\n"
    
    if categories['refinement']:
        content += "### 🔄 Refinement Prompts\n"
        content += "These prompts are used for iterative improvements:\n"
        for filename in categories['refinement']:
            content += f"- [`{filename}`](./{filename})\n"
        content += "\n"
    
    if categories['other']:
        content += "### 📄 Other Prompts\n"
        for filename in categories['other']:
            content += f"- [`{filename}`](./{filename})\n"
        content += "\n"
    
    content += """## 🎯 Usage Notes

- **Naive prompts**: Initial code generation from PyTorch
- **Optimization prompts**: Performance tuning and advanced optimizations  
- **Error prompts**: Debugging and fixing generated code
- **Refinement prompts**: Iterative improvement based on performance results

Each prompt file contains specific instructions that guide the AI models in their respective tasks during the kernel optimization process.
"""
    
    with open(summary_path, 'w') as f:
        f.write(content)

def append_to_evalboard(evalboard_path: str, run_name: str, new_entry: Dict[str, Any], sorted_entries: List[tuple]) -> None:
    """Append new entry to evalboard instead of regenerating the entire file."""
    # Read current content
    with open(evalboard_path, 'r') as f:
        content = f.read()
    
    # Check if this run already exists in the evalboard
    lines = content.split('\n')
    table_start_idx = -1
    table_end_idx = -1
    run_exists = False
    
    # Find table boundaries and check if run exists
    for i, line in enumerate(lines):
        if line.startswith('|') and 'Rank' in line:
            table_start_idx = i
        elif table_start_idx != -1 and line.startswith('|') and '---' in line:
            continue
        elif table_start_idx != -1 and line.startswith('|') and run_name in line:
            run_exists = True
            # If run exists, we need to regenerate the entire table with updated rankings
            regenerate_evalboard(evalboard_path, sorted_entries)
            return
        elif table_start_idx != -1 and not line.startswith('|') and line.strip():
            table_end_idx = i
            break
    
    # If run doesn't exist, append it to the table
    if not run_exists and table_start_idx != -1:
        # Find the current number of entries to determine rank
        current_rank = len([line for line in lines[table_start_idx:table_end_idx] if line.startswith('|') and '|' in line and 'Rank' not in line and '---' not in line])
        
        # Find the correct position to insert based on speedup ranking
        insert_position = table_end_idx if table_end_idx != -1 else len(lines)
        
        # Find correct rank position
        for rank, (entry_run_name, entry) in enumerate(sorted_entries, 1):
            if entry_run_name == run_name:
                new_row = f"| {rank}| {run_name} | {new_entry['date_version']} | {new_entry['overall_speedup']:.2f}x | {new_entry['success_rate']:.1f}% | {new_entry['avg_torch_time']:.1f} | {new_entry['avg_hip_time']:.1f} | {new_entry['total_kernels']} | {new_entry['configuration']} |"
                
                # If this is a top performer, we need to regenerate to update rankings
                if rank <= 3:
                    regenerate_evalboard(evalboard_path, sorted_entries)
                    return
                else:
                    # Insert at the end of the table
                    lines.insert(insert_position - 1, new_row)
                    break
        
        # Add performance chart section for the new entry
        chart_section_start = -1
        for i, line in enumerate(lines):
            if "## 📈 Performance Charts" in line:
                chart_section_start = i
                break
        
        if chart_section_start != -1 and 'plot_path' in new_entry and new_entry['plot_path']:
            relative_plot_path = os.path.relpath(new_entry['plot_path'], os.path.dirname(evalboard_path))
            chart_lines = [
                f"### {run_name}",
                "",
                f"#### Average Performance by Group",
                f"![{run_name} Performance Chart]({relative_plot_path})",
                ""
            ]
            
            # Add density plot if available
            if 'density_plot_path' in new_entry and new_entry['density_plot_path']:
                relative_density_path = os.path.relpath(new_entry['density_plot_path'], os.path.dirname(evalboard_path))
                chart_lines.extend([
                    f"#### Performance Distribution",
                    f"![{run_name} Density Plot]({relative_density_path})",
                    ""
                ])
            
            # Add additional density plots if available
            for suffix in ['1', '2']:
                density_key = f'density_plot_path_{suffix}'
                if density_key in new_entry and new_entry[density_key]:
                    relative_density_path = os.path.relpath(new_entry[density_key], os.path.dirname(evalboard_path))
                    chart_lines.extend([
                        f"#### Performance Distribution {suffix}",
                        f"![{run_name} Density Plot {suffix}]({relative_density_path})",
                        ""
                    ])
            
            # Find the end of charts section
            config_section_start = -1
            for i in range(chart_section_start, len(lines)):
                if "## ⚙️ Detailed Configuration" in lines[i]:
                    config_section_start = i
                    break
            
            insert_pos = config_section_start if config_section_start != -1 else len(lines)
            for j, chart_line in enumerate(chart_lines):
                lines.insert(insert_pos + j, chart_line)
        
        # Add detailed configuration section for the new entry
        config_section_start = -1
        for i, line in enumerate(lines):
            if "## ⚙️ Detailed Configuration" in line:
                config_section_start = i
                break
        
        if config_section_start != -1 and 'detailed_config' in new_entry:
            config = new_entry['detailed_config']
            config_lines = [
                f"### {run_name}",
                "",
                "#### Pipeline Settings"
            ]
            
            pipeline = config.get('pipeline', {})
            config_lines.extend([
                f"- **Kernel Language**: {pipeline.get('kernel_lang', 'unknown')}",
                f"- **RAG Enabled**: {'✅' if pipeline.get('rag_enabled') else '❌'}",
                f"- **Online Search**: {'✅' if pipeline.get('online_search') else '❌'}",
                f"- **Cheat Sheet**: {'✅' if pipeline.get('cheat_sheet') else '❌'}",
                f"- **Omnivise**: {'✅' if pipeline.get('omnivise') else '❌'}",
                f"- **Correctness Check**: {'✅' if pipeline.get('enable_correctness') else '❌'}",
                ""
            ])
            
            # Search Configuration
            search = config.get('search', {})
            if search.get('enabled'):
                config_lines.extend([
                    "#### Search Configuration",
                    f"- **Method**: {search.get('method', 'unknown')}"
                ])
                if search.get('method') == 'genetic':
                    config_lines.extend([
                        f"- **Population**: {search.get('pop', 'N/A')}",
                        f"- **Generations**: {search.get('ngen', 'N/A')}",
                        f"- **Patience**: {search.get('patience', 'N/A')}"
                    ])
                elif search.get('method') == 'bayes':
                    config_lines.append(f"- **Max Trials**: {search.get('max_trials', 'N/A')}")
                config_lines.append("")
            
            # Model Configuration
            models = config.get('models', {})
            config_lines.extend([
                "#### Model Configuration",
                f"- **Torch Analyser**: {models.get('torch_analyser', 'unknown')}",
                f"- **Kernel Generator**: {models.get('kernel_generator', 'unknown')}"
            ])
            if models.get('torch_analyser') != models.get('kernel_generator'):
                config_lines.append(f"- **Multi-Model Setup**: ✅ (Different models for analysis vs generation)")
            else:
                config_lines.append(f"- **Multi-Model Setup**: ❌ (Same model for both tasks)")
            config_lines.append("")
            
            # Add prompts section if available
            if 'prompts' in new_entry and new_entry['prompts']:
                config_lines.extend([
                    "#### Prompts Used",
                    f"📝 **Prompt Files**: [View all prompts](reports/{run_name}/prompts/PROMPTS_SUMMARY.md)",
                    ""
                ])
                
                # List key prompt files
                key_prompts = []
                for prompt_file in new_entry['prompts'].keys():
                    if any(keyword in prompt_file.lower() for keyword in ['naive', 'opt', 'error', 'refine']):
                        key_prompts.append(prompt_file)
                
                if key_prompts:
                    config_lines.append("**Key Prompt Files**:")
                    for prompt_file in sorted(key_prompts):
                        config_lines.append(f"- [`{prompt_file}`](reports/{run_name}/prompts/{prompt_file})")
                config_lines.append("")
            
            # Find insertion point (before Notes section)
            notes_section_start = -1
            for i in range(config_section_start, len(lines)):
                if "## 📝 Notes" in lines[i]:
                    notes_section_start = i
                    break
            
            insert_pos = notes_section_start if notes_section_start != -1 else len(lines)
            for j, config_line in enumerate(config_lines):
                lines.insert(insert_pos + j, config_line)
        
        # Update timestamp
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].startswith('*Last updated:'):
                lines[i] = f"*Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"
                break
        
        # Write updated content
        with open(evalboard_path, 'w') as f:
            f.write('\n'.join(lines))
        
        print(f"✅ Appended {run_name} to evalboard")
    else:
        # If we couldn't find the table or other issues, regenerate
        regenerate_evalboard(evalboard_path, sorted_entries)

def update_evalboard(run_name: str, config: Dict[str, Any], stats: Dict[str, float], 
                      input_folder: str, main_folder: str, mode: str = 'pytorch2kernel') -> None:
    """Update the evalboard markdown file by appending new entries instead of overwriting."""
    evalboard_path = os.path.join(main_folder, "evalboard.md")
    
    # Copy plots to reports folder (with language filtering)
    copied_plots = copy_plots_to_reports(run_name, input_folder, main_folder, config)
    
    # Use the appropriate plot path based on mode
    if mode == 'kernel2kernel':
        plot_path = copied_plots.get('kernel_optimization_summary.png', 
                                   copied_plots.get('kernel_optimization_comparison.png', 
                                   copied_plots.get('kernel_optimization_speedup_distribution.png', '')))
    else:
        plot_path = copied_plots.get('average_baseline_barplot_grouped_log.png', '')
    
    # Check if evalboard exists, if not create it
    if not os.path.exists(evalboard_path):
        create_evalboard_header(evalboard_path)
    
    # Read existing evalboard
    with open(evalboard_path, 'r') as f:
        content = f.read()
    
    # Extract existing entries
    entries = parse_existing_entries(content)
    
    # Reconstruct missing data for existing entries (plot paths, configs, etc.)
    _reconstruct_missing_entry_data(entries, main_folder)
    
    # Create new entry with mode information
    new_entry = create_entry(run_name, config, stats, plot_path, copied_plots, mode)
    
    # Add or update entry (this will preserve existing entries and add/update the new one)
    if run_name and run_name.strip() and run_name.strip().lower() != 'logs':  # Ensure run_name is not empty or just "logs"
        entries[run_name] = new_entry
    else:
        print(f"⚠️  Skipping entry with invalid run name: '{run_name}'")
        return
    
    # Filter out any entries with empty run names or "logs" that might have been parsed incorrectly
    entries = {k: v for k, v in entries.items() if k and k.strip() and k.strip().lower() != 'logs'}
    
    # Sort entries by overall speedup (descending)
    sorted_entries = sorted(entries.items(), key=lambda x: x[1]['overall_speedup'], reverse=True)
    
    # Always regenerate evalboard to ensure proper ranking and avoid duplication issues
    regenerate_evalboard(evalboard_path, sorted_entries)
    
    print(f"✅ Updated evalboard with {run_name} - Total entries: {len(entries)}")


def create_evalboard_header(evalboard_path: str) -> None:
    """Create the initial evalboard markdown file."""
    header = """# HIP Kernel Benchmark Leaderboard 🏆

Welcome to the HIP Kernel Benchmark Leaderboard! This page tracks the performance of different optimization runs across various kernel categories.

## 📊 Rankings

**Mode Legend:**
- **P2K**: PyTorch-to-Kernel generation
- **K2K**: Kernel-to-Kernel optimization

| Rank | Run Name | Date/Version | Mode | Overall Speedup | Success Rate (%) | Avg Torch Time (μs) | Avg HIP Time (μs) | Total Kernels | Configuration |
|------|----------|--------------|------|-----------------|------------------|-------------------|----------------|---------------|---------------|
"""
    
    with open(evalboard_path, 'w') as f:
        f.write(header)


def parse_existing_entries(content: str) -> Dict[str, Dict[str, Any]]:
    """Parse existing evalboard entries from markdown content."""
    entries = {}
    lines = content.split('\n')
    
    # Detect if this is the new format with Mode column
    has_mode_column = any('Mode' in line and 'Rank' in line for line in lines)
    
    # First pass: Parse table data
    in_table = False
    for line in lines:
        if line.startswith('|') and 'Rank' in line:
            in_table = True
            continue
        elif line.startswith('|') and '---' in line:
            continue
        elif in_table and line.startswith('|'):
            parts = [p.strip() for p in line.split('|')[1:-1]]  # Remove empty first/last elements
            
            # Adjust for new format with Mode column
            min_parts = 9 if has_mode_column else 8
            run_name_idx = 1
            date_idx = 2
            mode_idx = 3 if has_mode_column else None
            speedup_idx = 4 if has_mode_column else 3
            success_idx = 5 if has_mode_column else 4
            torch_time_idx = 6 if has_mode_column else 5
            hip_time_idx = 7 if has_mode_column else 6
            kernels_idx = 8 if has_mode_column else 7
            config_idx = 9 if has_mode_column else 8
            
            if len(parts) >= min_parts and parts[run_name_idx]:  # Ensure run_name is not empty
                try:
                    run_name = parts[run_name_idx]
                    # Skip if run_name is empty or just whitespace
                    if not run_name.strip():
                        continue
                        
                    # Remove "x" suffix from speedup if present
                    speedup_str = parts[speedup_idx].replace('x', '') if parts[speedup_idx].endswith('x') else parts[speedup_idx]
                    # Remove "%" suffix from success rate if present
                    success_rate_str = parts[success_idx].replace('%', '') if parts[success_idx].endswith('%') else parts[success_idx]
                    
                    # Safely convert numeric values
                    try:
                        overall_speedup = float(speedup_str) if speedup_str and speedup_str.strip() else 0.0
                        success_rate = float(success_rate_str) if success_rate_str and success_rate_str.strip() else 0.0
                        avg_torch_time = float(parts[torch_time_idx]) if parts[torch_time_idx] and parts[torch_time_idx].strip() else 0.0
                        avg_hip_time = float(parts[hip_time_idx]) if parts[hip_time_idx] and parts[hip_time_idx].strip() else 0.0
                        total_kernels = int(parts[kernels_idx]) if parts[kernels_idx] and parts[kernels_idx].strip() else 0
                    except ValueError as ve:
                        print(f"⚠️  Error parsing numeric values in row: {parts} - {ve}")
                        continue
                    
                    entry = {
                        'date_version': parts[date_idx],
                        'overall_speedup': overall_speedup,
                        'success_rate': success_rate,
                        'avg_torch_time': avg_torch_time,
                        'avg_hip_time': avg_hip_time,
                        'total_kernels': total_kernels,
                        'configuration': parts[config_idx] if len(parts) > config_idx else ""
                    }
                    
                    # Add mode if available
                    if has_mode_column and mode_idx and len(parts) > mode_idx:
                        mode_str = parts[mode_idx]
                        entry['mode'] = 'kernel2kernel' if mode_str == 'K2K' else 'pytorch2kernel'
                    
                    entries[run_name] = entry
                    
                except (ValueError, IndexError) as e:
                    print(f"⚠️  Skipping malformed table row: {line.strip()} - Error: {e}")
                    continue
        elif in_table and not line.startswith('|'):
            break
    
    # Second pass: Parse performance charts and detailed configurations
    _parse_performance_charts(content, entries)
    _parse_detailed_configurations(content, entries)
    
    return entries

def _parse_performance_charts(content: str, entries: Dict[str, Dict[str, Any]]) -> None:
    """Parse performance charts section and add plot paths to entries."""
    lines = content.split('\n')
    current_run = None
    
    for i, line in enumerate(lines):
        # Look for performance chart section headers
        if line.startswith('### ') and '## 📈 Performance Charts' in '\n'.join(lines[max(0, i-10):i]):
            current_run = line[4:].strip()  # Remove "### "
            if current_run in entries:
                entries[current_run]['plot_path'] = ''
                entries[current_run]['density_plot_path'] = ''
        
        # Look for plot references
        elif current_run and current_run in entries and line.startswith('!['):
            # Extract plot path from markdown image syntax
            start = line.find('](') + 2
            end = line.find(')', start)
            if start > 1 and end > start:
                plot_path = line[start:end]
                # Remove "kernel-agentic/" prefix if present
                if plot_path.startswith('kernel-agentic/'):
                    plot_path = plot_path[len('kernel-agentic/'):]
                
                if 'average_baseline_barplot_grouped_log' in plot_path:
                    entries[current_run]['plot_path'] = plot_path
                elif 'baseline_density_cleaned' in plot_path:
                    entries[current_run]['density_plot_path'] = plot_path

def _parse_detailed_configurations(content: str, entries: Dict[str, Dict[str, Any]]) -> None:
    """Parse detailed configuration section and add config details to entries."""
    lines = content.split('\n')
    current_run = None
    current_config = {}
    in_config_section = False
    
    for i, line in enumerate(lines):
        # Look for detailed config section
        if '## ⚙️ Detailed Configuration' in line:
            in_config_section = True
            continue
        elif in_config_section and line.startswith('## '):
            # End of detailed config section
            break
        elif in_config_section and line.startswith('### '):
            # Save previous config if exists
            if current_run and current_run in entries and current_config:
                entries[current_run]['detailed_config'] = current_config.copy()
            
            # Start new config
            current_run = line[4:].strip()  # Remove "### "
            current_config = {'pipeline': {}, 'search': {}, 'models': {}, 'summary': entries.get(current_run, {}).get('configuration', '')}
            
        elif current_run and current_run in entries and in_config_section:
            # Parse configuration details
            if line.startswith('- **Kernel Language**:'):
                current_config['pipeline']['kernel_lang'] = line.split(':')[1].strip()
            elif line.startswith('- **RAG Enabled**:'):
                current_config['pipeline']['rag_enabled'] = '✅' in line
            elif line.startswith('- **Online Search**:'):
                current_config['pipeline']['online_search'] = '✅' in line
            elif line.startswith('- **Cheat Sheet**:'):
                current_config['pipeline']['cheat_sheet'] = '✅' in line
            elif line.startswith('- **Omnivise**:'):
                current_config['pipeline']['omnivise'] = '✅' in line
            elif line.startswith('- **Correctness Check**:'):
                current_config['pipeline']['enable_correctness'] = '✅' in line
            elif line.startswith('- **Method**:'):
                current_config['search']['method'] = line.split(':')[1].strip()
                current_config['search']['enabled'] = True
            elif line.startswith('- **Population**:'):
                current_config['search']['pop'] = line.split(':')[1].strip()
            elif line.startswith('- **Generations**:'):
                current_config['search']['ngen'] = line.split(':')[1].strip()
            elif line.startswith('- **Patience**:'):
                current_config['search']['patience'] = line.split(':')[1].strip()
            elif line.startswith('- **Max Trials**:'):
                current_config['search']['max_trials'] = line.split(':')[1].strip()
            elif line.startswith('- **Torch Analyser**:'):
                current_config['models']['torch_analyser'] = line.split(':')[1].strip()
            elif line.startswith('- **Kernel Generator**:'):
                current_config['models']['kernel_generator'] = line.split(':')[1].strip()
            elif line.startswith('📝 **Prompt Files**:') and current_run:
                # Found prompts section - extract prompts info
                prompts_info = {}
                # Look for prompt file links in following lines
                for j in range(i+1, min(i+20, len(lines))):
                    if lines[j].startswith('- [`') and '`](' in lines[j]:
                        # Extract filename from [`filename`](path) format
                        start = lines[j].find('[`') + 2
                        end = lines[j].find('`]', start)
                        if start > 1 and end > start:
                            filename = lines[j][start:end]
                            prompts_info[filename] = True
                    elif lines[j].startswith('####') or lines[j].startswith('###'):
                        break
                if prompts_info:
                    entries[current_run]['prompts'] = prompts_info
    
    # Save last config if exists
    if current_run and current_run in entries and current_config:
        entries[current_run]['detailed_config'] = current_config


def create_entry(run_name: str, config: Dict[str, Any], stats: Dict[str, float], 
                plot_path: str, copied_plots: Dict[str, str] = None, mode: str = 'pytorch2kernel') -> Dict[str, Any]:
    """Create a new evalboard entry."""
    date_version = extract_date_from_folder(run_name)
    
    # Extract detailed configuration information
    config_details = extract_detailed_config(config, mode)
    
    entry = {
        'date_version': date_version,
        'overall_speedup': stats['overall_speedup'],
        'success_rate': stats['success_rate'],
        'avg_torch_time': stats['avg_torch_time'],
        'avg_hip_time': stats['avg_hip_time'],
        'total_kernels': stats['total_kernels'],
        'configuration': config_details['summary'],
        'detailed_config': config_details,
        'plot_path': plot_path,
        'mode': mode
    }
    
    # Add additional plot paths if available
    if copied_plots:
        entry['density_plot_path'] = copied_plots.get('baseline_density_cleaned.png', '')
        # Add optimization-specific plots for kernel2kernel mode
        if mode == 'kernel2kernel':
            entry['speedup_plot_path'] = copied_plots.get('kernel_optimization_speedup_distribution.png', '')
            entry['comparison_plot_path'] = copied_plots.get('kernel_optimization_comparison.png', '')
        # Add prompts information if available
        if 'prompts' in copied_plots:
            entry['prompts'] = copied_plots['prompts']
        # Store all copied files for access to reports
        entry['copied_files'] = copied_plots
    
    return entry


def extract_detailed_config(config: Dict[str, Any], mode: str = 'pytorch2kernel') -> Dict[str, Any]:
    """Extract detailed configuration information for the leaderboard."""
    details = {
        'pipeline': {},
        'models': {},
        'search': {},
        'summary': "",
        'mode': mode
    }
    
    # Extract Pipeline configuration
    if 'Pipeline' in config:
        pipeline = config['Pipeline']
        details['pipeline'] = {
            'kernel_lang': pipeline.get('kernel_lang', 'unknown'),
            'mode': pipeline.get('mode', mode),
            'rag_enabled': pipeline.get('rag_enabled', False),
            'online_search': pipeline.get('online_search', False),
            'cheat_sheet': pipeline.get('cheat_sheet', False),
            'omnivise': pipeline.get('omnivise', False),
            'enable_correctness': pipeline.get('enable_correctness', False)
        }
        
        # Extract search configuration
        if 'search' in pipeline:
            search_config = pipeline['search']
            details['search'] = {
                'enabled': search_config.get('enabled', False),
                'method': search_config.get('method', 'unknown'),
                'patience': search_config.get('patience', 'N/A'),
                'max_trials': search_config.get('max_trials', 'N/A'),
                'pop': search_config.get('pop', 'N/A'),
                'ngen': search_config.get('ngen', 'N/A')
            }
    # Extract model configuration
    if 'openai' in config:
        openai_config = config['openai']
        
        # Default model
        details['models']['default'] = openai_config.get('model_name', 'unknown')
        
        # Agent-specific models
        if 'agent_models' in openai_config:
            agent_models = openai_config['agent_models']
            details['models']['torch_analyser'] = agent_models.get('torch_analyser', {}).get('model_name', details['models']['default'])
            details['models']['kernel_generator'] = agent_models.get('kernel_generator', {}).get('model_name', details['models']['default'])
        else:
            # If no agent-specific models, use default for both
            details['models']['torch_analyser'] = details['models']['default']
            details['models']['kernel_generator'] = details['models']['default']
    
    # Create summary for table display
    summary_parts = []
    # Add mode indicator
    if mode == 'kernel2kernel':
        summary_parts.append("Mode: K2K")
    else:
        summary_parts.append("Mode: P2K")
    
    if details['pipeline'].get('kernel_lang'):
        summary_parts.append(f"Lang: {details['pipeline']['kernel_lang']}")
    if details['search'].get('method'):
        summary_parts.append(f"Search: {details['search']['method']}")
    if details['models'].get('kernel_generator'):
        summary_parts.append(f"Gen: {details['models']['kernel_generator']}")
    if details['models'].get('torch_analyser') != details['models'].get('kernel_generator'):
        summary_parts.append(f"Ana: {details['models']['torch_analyser']}")
    
    details['summary'] = ", ".join(summary_parts[:4])  # Limit to 4 items for readability
    
    return details


def regenerate_evalboard(evalboard_path: str, sorted_entries: List[tuple]) -> None:
    """Regenerate the entire evalboard file."""
    content = """# HIP Kernel Benchmark evalboard 🏆

Welcome to the HIP Kernel Benchmark evalboard! This page tracks the performance of different optimization runs across various kernel categories.

## 📊 Rankings

**Mode Legend:**
- **P2K**: PyTorch-to-Kernel generation
- **K2K**: Kernel-to-Kernel optimization

| Rank | Run Name | Date/Version | Mode | Overall Speedup | Success Rate (%) | Avg Torch Time (μs) | Avg HIP Time (μs) | Total Kernels | Configuration |
|------|----------|--------------|------|-----------------|------------------|-------------------|----------------|---------------|---------------|
"""
    
    for rank, (run_name, entry) in enumerate(sorted_entries, 1):
        # Only add entries with valid run names
        if run_name and run_name.strip():
            mode_display = "K2K" if entry.get('mode') == 'kernel2kernel' else "P2K"
            content += f"| {rank}| {run_name} | {entry['date_version']} | {mode_display} | {entry['overall_speedup']:.2f}x | {entry['success_rate']:.1f}% | {entry['avg_torch_time']:.1f} | {entry['avg_hip_time']:.1f} | {entry['total_kernels']} | {entry['configuration']} |\n"
    
    content += "\n## 📈 Performance Charts\n\n"
    
    # Show performance charts for ALL runs with valid data
    chart_count = 0
    for run_name, entry in sorted_entries:
        if run_name and run_name.strip() and 'plot_path' in entry and entry['plot_path']:
            relative_plot_path = os.path.relpath(entry['plot_path'], os.path.dirname(evalboard_path))
            relative_plot_path=relative_plot_path.replace('kernel-agentic/', '')
            content += f"### {run_name}\n\n"
            
            # Different content based on mode
            if entry.get('mode') == 'kernel2kernel':
                content += f"#### Kernel Optimization Results\n"
                content += f"![{run_name} Optimization Chart]({relative_plot_path})\n\n"
                
                # Add additional optimization plots if available
                if 'speedup_plot_path' in entry and entry['speedup_plot_path']:
                    relative_speedup_path = os.path.relpath(entry['speedup_plot_path'], os.path.dirname(evalboard_path))
                    relative_speedup_path = relative_speedup_path.replace('kernel-agentic/', '')
                    content += f"#### Speedup Distribution\n"
                    content += f"![{run_name} Speedup Distribution]({relative_speedup_path})\n\n"
                    
                if 'comparison_plot_path' in entry and entry['comparison_plot_path']:
                    relative_comparison_path = os.path.relpath(entry['comparison_plot_path'], os.path.dirname(evalboard_path))
                    relative_comparison_path = relative_comparison_path.replace('kernel-agentic/', '')
                    content += f"#### Performance Comparison\n"
                    content += f"![{run_name} Performance Comparison]({relative_comparison_path})\n\n"
            else:
                content += f"#### Average Performance by Group\n"
                content += f"![{run_name} Performance Chart]({relative_plot_path})\n\n"
                
                # Add density plot if available
                if 'density_plot_path' in entry and entry['density_plot_path']:
                    relative_density_path = os.path.relpath(entry['density_plot_path'], os.path.dirname(evalboard_path))
                    relative_density_path = relative_density_path.replace('kernel-agentic/', '')
                    content += f"#### Performance Distribution\n"
                    content += f"![{run_name} Density Plot]({relative_density_path})\n\n"
            
            chart_count += 1
    
    if chart_count == 0:
        content += "*No performance charts available yet.*\n\n"
    else:
        content += f"*Showing {chart_count} performance chart(s)*\n\n"
    
    # Add kernel optimization reports section for K2K runs
    content += "\n## 📋 Kernel Optimization Reports\n\n"
    
    optimization_reports = 0
    for run_name, entry in sorted_entries:
        if (run_name and run_name.strip() and 
            entry.get('mode') == 'kernel2kernel' and 
            'kernel_optimization_report.md' in entry.get('copied_files', {})):
            
            # Read and embed the optimization report content
            report_path = entry['copied_files']['kernel_optimization_report.md']
            if os.path.exists(report_path):
                try:
                    with open(report_path, 'r') as f:
                        report_content = f.read()
                    
                    # Add a subsection for this run's optimization report
                    content += f"### {run_name} - Kernel Optimization Analysis\n\n"
                    content += report_content + "\n\n"
                    optimization_reports += 1
                    
                except Exception as e:
                    print(f"⚠️  Error reading optimization report for {run_name}: {e}")
                    content += f"### {run_name}\n\n*Error loading optimization report*\n\n"
    
    if optimization_reports == 0:
        content += "*No kernel optimization reports available yet.*\n\n"
    else:
        content += f"*Showing {optimization_reports} optimization report(s)*\n\n"
            
    
    # Add detailed configuration section
    content += "\n## ⚙️ Detailed Configuration\n\n"
    
    # Show detailed configuration for ALL runs with valid data
    config_count = 0
    for run_name, entry in sorted_entries:
        if run_name and run_name.strip() and 'detailed_config' in entry:
            config = entry['detailed_config']
            content += f"### {run_name}\n\n"
            
            # Pipeline Configuration
            content += "#### Pipeline Settings\n"
            pipeline = config.get('pipeline', {})
            content += f"- **Kernel Language**: {pipeline.get('kernel_lang', 'unknown')}\n"
            content += f"- **RAG Enabled**: {'✅' if pipeline.get('rag_enabled') else '❌'}\n"
            content += f"- **Online Search**: {'✅' if pipeline.get('online_search') else '❌'}\n"
            content += f"- **Cheat Sheet**: {'✅' if pipeline.get('cheat_sheet') else '❌'}\n"
            content += f"- **Omnivise**: {'✅' if pipeline.get('omnivise') else '❌'}\n"
            content += f"- **Correctness Check**: {'✅' if pipeline.get('enable_correctness') else '❌'}\n\n"
            
            # Search Configuration
            search = config.get('search', {})
            if search.get('enabled'):
                content += "#### Search Configuration\n"
                content += f"- **Method**: {search.get('method', 'unknown')}\n"
                if search.get('method') == 'genetic':
                    content += f"- **Population**: {search.get('pop', 'N/A')}\n"
                    content += f"- **Generations**: {search.get('ngen', 'N/A')}\n"
                    content += f"- **Patience**: {search.get('patience', 'N/A')}\n"
                elif search.get('method') == 'bayes':
                    content += f"- **Max Trials**: {search.get('max_trials', 'N/A')}\n"
                content += "\n"
            
            # Model Configuration
            models = config.get('models', {})
            content += "#### Model Configuration\n"
            content += f"- **Torch Analyser**: {models.get('torch_analyser', 'unknown')}\n"
            content += f"- **Kernel Generator**: {models.get('kernel_generator', 'unknown')}\n"
            if models.get('torch_analyser') != models.get('kernel_generator'):
                content += f"- **Multi-Model Setup**: ✅ (Different models for analysis vs generation)\n"
            else:
                content += f"- **Multi-Model Setup**: ❌ (Same model for both tasks)\n"
            content += "\n"
            
            # Add prompts section if available
            if 'prompts' in entry and entry['prompts']:
                content += "#### Prompts Used\n"
                prompts_folder_path = os.path.join("reports", run_name, "prompts")
                content += f"📝 **Prompt Files**: [View all prompts]({prompts_folder_path}/PROMPTS_SUMMARY.md)\n\n"
                
                # List key prompt files
                key_prompts = []
                for prompt_file in entry['prompts'].keys():
                    if any(keyword in prompt_file.lower() for keyword in ['naive', 'opt', 'error', 'refine']):
                        key_prompts.append(prompt_file)
                
                if key_prompts:
                    content += "**Key Prompt Files**:\n"
                    for prompt_file in sorted(key_prompts):
                        prompt_path = os.path.join(prompts_folder_path, prompt_file)
                        content += f"- [`{prompt_file}`]({prompt_path})\n"
                content += "\n"
            
            # Add cheat sheets section if available
            cheat_sheets_path = os.path.join("reports", run_name, "cheat_sheets")
            if os.path.exists(os.path.join(os.path.dirname(evalboard_path), cheat_sheets_path)):
                content += "#### Cheat Sheets\n"
                content += f"📋 **Cheat Sheets**: [View cheat sheets]({cheat_sheets_path}/)\n\n"
            
            config_count += 1
    
    if config_count == 0:
        content += "*No detailed configurations available yet.*\n\n"
    else:
        content += f"*Showing {config_count} detailed configuration(s)*\n\n"
    
    content += f"""
## 📝 Notes

- **Overall Speedup**: Harmonic mean of individual kernel speedups (Torch time / HIP time)
- **Success Rate**: Percentage of kernels that successfully compiled and executed
- **Avg Times**: Average execution times across all kernels in microseconds
- **Configuration**: Key settings used for the optimization run
- **Prompts**: Links to actual prompt files used during optimization

---
*Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""
    
    with open(evalboard_path, 'w') as f:
        f.write(content)

def generate_summary_stats(list_baselines_flat: List[float], list_best_flat: List[float]) -> Dict[str, float]:
    """Generate summary statistics from baseline and best times."""
    if not list_baselines_flat or not list_best_flat:
        return {
            'total_kernels': 0,
            'successful_kernels': 0,
            'success_rate': 0.0,
            'overall_speedup': 0.0,
            'avg_torch_time': 0.0,
            'avg_hip_time': 0.0
        }
    
    total_kernels = len(list_baselines_flat)
    successful_kernels = len([t for t in list_best_flat if t is not None and t > 0])
    success_rate = (successful_kernels / total_kernels) * 100 if total_kernels > 0 else 0
    
    # Calculate harmonic mean speedup (only for successful kernels)
    valid_speedups = []
    for i in range(len(list_baselines_flat)):
        if (i < len(list_best_flat) and 
            list_best_flat[i] is not None and 
            list_best_flat[i] > 0 and
            list_baselines_flat[i] is not None and
            list_baselines_flat[i] > 0):
            speedup = list_baselines_flat[i] / list_best_flat[i]
            valid_speedups.append(speedup)
    
    if valid_speedups:
        # Harmonic mean of speedups
        harmonic_mean_speedup = len(valid_speedups) / sum(1/s for s in valid_speedups)
    else:
        harmonic_mean_speedup = 0.0
    
    # Filter out None values for average calculations
    valid_baselines = [t for t in list_baselines_flat if t is not None]
    valid_best_times = [t for t in list_best_flat if t is not None]
    
    avg_torch_time = np.mean(valid_baselines) if valid_baselines else 0.0
    avg_hip_time = np.mean(valid_best_times) if valid_best_times else 0.0
    
    return {
        'total_kernels': total_kernels,
        'successful_kernels': successful_kernels,
        'success_rate': success_rate,
        'overall_speedup': harmonic_mean_speedup,
        'avg_torch_time': avg_torch_time,
        'avg_hip_time': avg_hip_time
    }


def main(input, grouping_file=None, level='level_1'):
    # Detect run mode first
    run_mode = detect_run_mode(input)
    print(f"🔍 Detected run mode: {run_mode}")
    
    if run_mode == 'kernel2kernel':
        # Process kernel optimization results
        print("📊 Processing kernel2kernel optimization results...")
        return process_kernel_optimization_mode(input, grouping_file, level)
    else:
        # Process pytorch2kernel results (existing logic)
        print("📊 Processing pytorch2kernel generation results...")
        return process_pytorch2kernel_mode(input, grouping_file, level)

def process_pytorch2kernel_mode(input, grouping_file=None, level='level_1'):
    """Process traditional PyTorch to kernel generation results."""
    # Load grouping configuration from specified file or default location
    if grouping_file and os.path.isabs(grouping_file):
        # If absolute path is provided, use it directly
        grouping_path = grouping_file
    elif grouping_file:
        # If relative path is provided, resolve it relative to current working directory
        grouping_path = os.path.abspath(grouping_file)
    else:
        # Default behavior - adjust path relative to script location
        script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        grouping_path = os.path.join(script_dir, "grouping.yml")
    
    grouping_config = load_grouping_config(grouping_path)
    level_data = grouping_config.get(level, {})
    
    if not level_data:
        print(f"❌ No data found for level '{level}' in grouping configuration!")
        print(f"Available levels: {list(grouping_config.keys())}")
        return
    
    print(f"✅ Loaded {len(level_data)} groups from {level} in {grouping_path}")
    
    # Create a mapping from group names to indices
    group_name_to_index = {group: idx for idx, group in enumerate(level_data.keys())}

    # Initialize list_baselines with one empty list per group
    list_baselines = [[] for _ in range(len(level_data))]
    list_best = [[] for _ in range(len(level_data))]
    list_fails = [[0] for _ in range(len(level_data))]

    folders = os.listdir(input)
    total_count = 0
    for folder in folders:
        if folder.endswith('.jsonl') or folder.endswith('.png') or folder.endswith('.json'):
            continue

        name = folder.split('_', 1)[-1].split('.py', 1)[0]
        files = os.listdir(os.path.join(input, folder))

        baseline_files = [f for f in files if f.startswith('baseline')]
        if not baseline_files:
            print(f"No baseline file found in {folder}")
            continue

        baseline = baseline_files[0]
        with open(os.path.join(input, folder, baseline), 'r') as f:
            data = json.load(f)
        baseline_time = data['lat_us']
        
        cpp_files = [f for f in os.listdir(os.path.join(input,folder)) if f.endswith('.cpp')]
        Failed =not (len(cpp_files) > 0)

        jsonl_path = os.path.join(input,folder, "generation_history.jsonl")
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

        # Match name to group and insert baseline_time at the correct index
        for group, names in level_data.items():
            if name in names:
                group_index = group_name_to_index[group]
                list_baselines[group_index].append(baseline_time)
                list_best[group_index].append(baseline_time if best_sft_entry is None else best_sft_entry['hip_us'])
                list_fails[group_index][0] += 1 if Failed else 0
                break
        else:
            print(f"Name {name} not found in any group")


    # Step 1: Calculate average baseline time per group
    group_names = list(level_data.keys())
    average_times = [np.mean(times) if times else 0 for times in list_baselines]
    average_best_times = [np.mean(times) if times else 0 for times in list_best]

    # Step 2: Remove 'groupX_' prefix from group names
    cleaned_names = [name.split('_', 1)[-1] if '_' in name else name for name in group_names]

    # Step 3: Calculate speedup ratios
    speedup_ratios = []
    for i in range(len(average_times)):
        if average_times[i] > 0 and average_best_times[i] > 0:
            speedup_ratios.append(average_times[i] / average_best_times[i])
        else:
            speedup_ratios.append(0.0)  # No speedup if no data

    # Create DataFrame
    df = pd.DataFrame({
        'Group': cleaned_names,
        'Avg Torch Time (μs)': average_times,
        'Avg HIP Time (μs)': average_best_times,
        'Speedup': speedup_ratios
    })

    # Filter out groups with no data
    df = df[df['Avg Torch Time (μs)'] > 0]

    if df.empty:
        print("No data available for plotting")
        return

    # Generate plots
    generate_performance_plots(df, input)
    
    # Generate summary statistics for evalboard
    list_baselines_flat = [time for sublist in list_baselines for time in sublist]
    list_best_flat = [time for sublist in list_best for time in sublist]
    stats = generate_summary_stats(list_baselines_flat, list_best_flat)
    
    # Load config for evalboard update
    config_path = os.path.join(os.path.dirname(input), "config.yml")
    if not os.path.exists(config_path):
        config_path = "config.yml"
    config = load_config(config_path)
    
    # Get the main folder (parent directory of logs)
    main_folder = os.path.dirname(os.path.abspath(input))
    if main_folder.endswith('/kernel-agentic'):
        main_folder = os.path.dirname(main_folder)  # Go up one more level if needed
    
    # Get run name from input path
    run_name = os.path.basename(input)
    
    # Update evalboard (this will copy plots to reports folder)
    update_evalboard(run_name, config, stats, input, main_folder, mode='pytorch2kernel')
    
    print(f"\n   Run Summary for {run_name}:")
    print(f"   Total Kernels: {stats['total_kernels']}")
    print(f"   Successful Kernels: {stats['successful_kernels']}")
    print(f"   Success Rate: {stats['success_rate']:.1f}%")
    print(f"   Overall Speedup: {stats['overall_speedup']:.2f}x")
    print(f"   Average Torch Time: {stats['avg_torch_time']:.1f} μs")
    print(f"   Average HIP Time: {stats['avg_hip_time']:.1f} μs")
    print(f"\n  evalboard updated at: {os.path.join(main_folder, 'evalboard.md')}")
    print(f"  Performance charts stored in: {os.path.join(main_folder, 'reports', run_name)}")
    print(f"  Prompts archived in: {os.path.join(main_folder, 'reports', run_name, 'prompts')}")


def generate_performance_plots(df: pd.DataFrame, input_folder: str) -> None:
    """Generate performance visualization plots."""
    # Set seaborn style
    sns.set_style("whitegrid")
    plt.rcParams['figure.figsize'] = (14, 10)

    # Create the main figure with subplots
    fig = plt.figure(figsize=(16, 12))
    gs = gridspec.GridSpec(2, 2, height_ratios=[2, 1], width_ratios=[3, 1])

    # Main bar plot (top-left, spanning two columns)
    ax1 = fig.add_subplot(gs[0, :])
    
    # Sort by speedup for better visualization
    df_sorted = df.sort_values('Speedup', ascending=False)
    
    # Create grouped bar chart
    x = np.arange(len(df_sorted))
    width = 0.35

    # Convert to log scale for better visualization if values are very different
    torch_times = df_sorted['Avg Torch Time (μs)'].values
    hip_times = df_sorted['Avg HIP Time (μs)'].values
    
    bars1 = ax1.bar(x - width/2, torch_times, width, label='Torch (Baseline)', 
                    color='#ff7f7f', alpha=0.8, edgecolor='black', linewidth=0.5)
    bars2 = ax1.bar(x + width/2, hip_times, width, label='HIP (Optimized)', 
                    color='#7fbf7f', alpha=0.8, edgecolor='black', linewidth=0.5)

    # Add speedup annotations
    for i, (torch_time, hip_time, speedup) in enumerate(zip(torch_times, hip_times, df_sorted['Speedup'])):
        if speedup > 0:  # Show annotation for any valid speedup value
            # Choose color based on speedup performance
            if speedup > 1:
                color = 'darkgreen'  # Good speedup
                annotation_text = f'{speedup:.1f}x'
            elif speedup < 1:
                color = 'darkred'    # Slower than baseline
                annotation_text = f'{speedup:.2f}x'
            else:
                color = 'darkorange' # Same performance
                annotation_text = f'{speedup:.1f}x'
                
            ax1.annotate(annotation_text, 
                        xy=(i, max(torch_time, hip_time)), 
                        xytext=(0, 10), textcoords='offset points',
                        ha='center', va='bottom', fontweight='bold',
                        fontsize=9, color=color)

    ax1.set_xlabel('Kernel Groups', fontsize=12, fontweight='bold')
    ax1.set_ylabel('Average Execution Time (μs)', fontsize=12, fontweight='bold')
    ax1.set_title('Average Performance by Kernel Group', 
                  fontsize=14, fontweight='bold', pad=20)
    ax1.set_xticks(x)
    ax1.set_xticklabels(df_sorted['Group'], rotation=45, ha='right')
    ax1.legend(fontsize=11)
    ax1.grid(True, alpha=0.3)
    
    # Use log scale if there's a large range in values
    if max(torch_times) / min(hip_times) > 100:
        ax1.set_yscale('log')
        ax1.set_ylabel('Average Execution Time (μs) - Log Scale', fontsize=12, fontweight='bold')

    # Speedup distribution (bottom-left)
    ax2 = fig.add_subplot(gs[1, 0])
    speedups = df_sorted['Speedup'].values
    speedups_clean = speedups[speedups > 0]  # Remove zero speedups
    
    if len(speedups_clean) > 0:
        # Create histogram
        ax2.hist(speedups_clean, bins=max(5, len(speedups_clean)//3), 
                alpha=0.7, color='skyblue', edgecolor='black', linewidth=0.5)
        ax2.axvline(np.mean(speedups_clean), color='red', linestyle='--', 
                   label=f'Mean: {np.mean(speedups_clean):.2f}x')
        ax2.axvline(np.median(speedups_clean), color='orange', linestyle='--', 
                   label=f'Median: {np.median(speedups_clean):.2f}x')
        ax2.set_xlabel('Speedup Ratio', fontsize=11)
        ax2.set_ylabel('Frequency', fontsize=11)
        ax2.set_title('Speedup Distribution', fontsize=12, fontweight='bold')
        ax2.legend(fontsize=10)
        ax2.grid(True, alpha=0.3)

    # Summary statistics (bottom-right)
    ax3 = fig.add_subplot(gs[1, 1])
    ax3.axis('off')  # Turn off axis
    
    # Calculate statistics
    total_groups = len(df)
    successful_groups = len(df[df['Speedup'] > 1])
    avg_speedup = np.mean(speedups_clean) if len(speedups_clean) > 0 else 0
    max_speedup = np.max(speedups_clean) if len(speedups_clean) > 0 else 0
    
    stats_text = f"""
📊 Performance Summary

Total Groups: {total_groups}
Improved Groups: {successful_groups}
Success Rate: {(successful_groups/total_groups)*100:.1f}%

Average Speedup: {avg_speedup:.2f}x
Maximum Speedup: {max_speedup:.2f}x
Median Speedup: {np.median(speedups_clean):.2f}x

Best Performing Group:
{df_sorted.iloc[0]['Group']}
({df_sorted.iloc[0]['Speedup']:.2f}x speedup)
"""
    
    ax3.text(0.05, 0.95, stats_text, transform=ax3.transAxes, fontsize=10,
             verticalalignment='top', bbox=dict(boxstyle='round', facecolor='lightblue', alpha=0.8))

    plt.tight_layout()
    
    # Save the plot
    output_path = os.path.join(input_folder, 'average_baseline_barplot_grouped_log.png')
    plt.savefig(output_path, dpi=300, bbox_inches='tight', facecolor='white')
    plt.close()
    
    print(f"📊 Performance chart saved: {output_path}")

    # Generate density plot
    generate_density_plot(df, input_folder)


def generate_density_plot(df: pd.DataFrame, input_folder: str) -> None:
    """Generate a density plot of performance improvements."""
    plt.figure(figsize=(12, 8))
    
    # Prepare data for density plot
    speedups = df['Speedup'].values
    speedups_clean = speedups[speedups > 0]
    
    if len(speedups_clean) > 1:
        # Create density plot
        kde = gaussian_kde(speedups_clean)
        x_range = np.linspace(speedups_clean.min(), speedups_clean.max(), 1000)
        density = kde(x_range)
        
        plt.fill_between(x_range, density, alpha=0.6, color='lightcoral', label='Speedup Density')
        plt.plot(x_range, density, color='darkred', linewidth=2)
        
        # Add vertical lines for statistics
        plt.axvline(np.mean(speedups_clean), color='blue', linestyle='--', linewidth=2,
                   label=f'Mean: {np.mean(speedups_clean):.2f}x')
        plt.axvline(np.median(speedups_clean), color='green', linestyle='--', linewidth=2,
                   label=f'Median: {np.median(speedups_clean):.2f}x')
        plt.axvline(1.0, color='black', linestyle='-', linewidth=1, alpha=0.5,
                   label='Baseline (1.0x)')
        
        plt.xlabel('Speedup Ratio', fontsize=12, fontweight='bold')
        plt.ylabel('Density', fontsize=12, fontweight='bold')
        plt.title('Distribution of Performance Improvements\n(Kernel Speedup Density)', 
                 fontsize=14, fontweight='bold')
        plt.legend(fontsize=11)
        plt.grid(True, alpha=0.3)
        
        # Add annotations for best performers
        top_performers = df.nlargest(3, 'Speedup')
        for i, (_, row) in enumerate(top_performers.iterrows()):
            plt.annotate(f'{row["Group"][:15]}...\n{row["Speedup"]:.1f}x', 
                        xy=(row['Speedup'], kde(row['Speedup'])[0]), 
                        xytext=(20, 20 + i*30), textcoords='offset points',
                        bbox=dict(boxstyle='round,pad=0.3', fc='yellow', alpha=0.7),
                        arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0'))
    
    else:
        plt.text(0.5, 0.5, 'Insufficient data for density plot', 
                transform=plt.gca().transAxes, ha='center', va='center', fontsize=16)
    
    plt.tight_layout()
    
    # Save the density plot
    output_path = os.path.join(input_folder, 'baseline_density_cleaned.png')
    plt.savefig(output_path, dpi=300, bbox_inches='tight', facecolor='white')
    plt.close()
    
    print(f"📈 Density plot saved: {output_path}")

def _reconstruct_missing_entry_data(entries: Dict[str, Dict[str, Any]], main_folder: str) -> None:
    """Reconstruct missing plot paths and detailed configurations for existing entries."""
    for run_name, entry in entries.items():
        # Skip if this entry already has plot paths and detailed config
        if 'plot_path' in entry and 'detailed_config' in entry:
            continue
            
        print(f"🔄 Reconstructing missing data for {run_name}")
        
        # Try to find plots in reports folder
        reports_folder = os.path.join(main_folder, "reports", run_name)
        if os.path.exists(reports_folder):
            # Check for plot files
            barplot_path = os.path.join(reports_folder, "average_baseline_barplot_grouped_log.png")
            density_path = os.path.join(reports_folder, "baseline_density_cleaned.png")
            
            if os.path.exists(barplot_path):
                entry['plot_path'] = os.path.relpath(barplot_path, main_folder)
                print(f"📊 Found barplot for {run_name}: {entry['plot_path']}")
            
            if os.path.exists(density_path):
                entry['density_plot_path'] = os.path.relpath(density_path, main_folder)
                print(f"📈 Found density plot for {run_name}: {entry['density_plot_path']}")
            
            # Check for prompts
            prompts_folder = os.path.join(reports_folder, "prompts")
            if os.path.exists(prompts_folder):
                prompts_info = {}
                for filename in os.listdir(prompts_folder):
                    if filename.endswith(('.txt', '.md', '.prompt')) and filename != 'PROMPTS_SUMMARY.md':
                        prompts_info[filename] = True
                if prompts_info:
                    entry['prompts'] = prompts_info
                    print(f"📝 Found {len(prompts_info)} prompts for {run_name}")
        
        # Try to reconstruct detailed config from configuration string if missing
        if 'detailed_config' not in entry and 'configuration' in entry:
            config_str = entry['configuration']
            entry['detailed_config'] = _parse_config_string(config_str)
            print(f"⚙️ Reconstructed config for {run_name}")

def _parse_config_string(config_str: str) -> Dict[str, Any]:
    """Parse a configuration string like 'Lang: hip, Search: genetic, Gen: o3, Ana: GPT-4o' into detailed config."""
    config = {
        'pipeline': {},
        'search': {},
        'models': {},
        'summary': config_str
    }
    
    # Parse the configuration string
    parts = [part.strip() for part in config_str.split(',')]
    for part in parts:
        if ':' in part:
            key, value = part.split(':', 1)
            key, value = key.strip(), value.strip()
            
            if key.lower() == 'lang':
                config['pipeline']['kernel_lang'] = value
            elif key.lower() == 'search':
                config['search']['method'] = value
                config['search']['enabled'] = True
            elif key.lower() == 'gen':
                config['models']['kernel_generator'] = value
            elif key.lower() == 'ana':
                config['models']['torch_analyser'] = value
    

    config['pipeline'].update({
        'rag_enabled': False,
        'online_search': False,
        'cheat_sheet': True,  
        'omnivise': True,     
        'enable_correctness': False
    })
    
    if config['search'].get('enabled'):
        config['search'].update({
            'pop': 'N/A',
            'ngen': 'N/A', 
            'patience': 'N/A',
            'max_trials': 'N/A'
        })
    
    return config

def detect_run_mode(input_folder: str) -> str:
    """
    Detect whether the logs are from pytorch2kernel or kernel2kernel mode.
    
    Returns:
        'pytorch2kernel' for normal PyTorch to kernel generation
        'kernel2kernel' for kernel optimization runs
        'mixed' for mixed content
    """
    folders = [f for f in os.listdir(input_folder) if os.path.isdir(os.path.join(input_folder, f))]
    
    pytorch2kernel_count = 0
    kernel2kernel_count = 0
    
    for folder in folders:
        # Check for kernel optimization pattern (ends with _opt)
        if folder.endswith('_opt'):
            kernel2kernel_count += 1
        else:
            # Check for PyTorch pattern (has baseline_ files)
            folder_path = os.path.join(input_folder, folder)
            if os.path.isdir(folder_path):
                files = os.listdir(folder_path)
                if any(f.startswith('baseline_') for f in files):
                    pytorch2kernel_count += 1
    
    if kernel2kernel_count > pytorch2kernel_count:
        return 'kernel2kernel'
    elif pytorch2kernel_count > 0:
        return 'pytorch2kernel'
    else:
        return 'mixed'

def process_kernel_optimization_results(input_folder: str, grouping_config: Dict = None) -> Dict[str, Any]:
    """
    Process kernel2kernel optimization results and generate statistics.
    
    Returns a dictionary with processed data for visualization.
    """
    folders = [f for f in os.listdir(input_folder) if os.path.isdir(os.path.join(input_folder, f)) and f.endswith('_opt')]
    
    results = {
        'optimization_results': [],
        'summary_stats': {},
        'speedup_improvements': [],
        'optimization_events': []
    }
    
    total_kernels = 0
    successful_optimizations = 0
    total_baseline_time = 0
    total_optimized_time = 0
    speedup_ratios = []
    
    for folder in folders:
        folder_path = os.path.join(input_folder, folder)
        
        # Extract kernel name (remove _opt suffix)
        kernel_name = folder.replace('_opt', '')
        
        # Look for the best optimized kernel JSON file
        json_files = [f for f in os.listdir(folder_path) if f.endswith('_optimized.json')]
        
        if not json_files:
            print(f"⚠️  No optimization results found in {folder}")
            continue
            
        json_file = json_files[0]
        json_path = os.path.join(folder_path, json_file)
        
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
            
            baseline_us = data.get('baseline_us', 0) or 0
            speedup = data.get('speedup', 0) or 0
            stats = data.get('stats', {})
            optimized_us = stats.get('avg_us', 0) or 0
            
            # Ensure all values are numeric
            baseline_us = float(baseline_us) if baseline_us is not None else 0.0
            speedup = float(speedup) if speedup is not None else 0.0
            optimized_us = float(optimized_us) if optimized_us is not None else 0.0
            
            result = {
                'kernel_name': kernel_name,
                'baseline_us': baseline_us,
                'optimized_us': optimized_us,
                'speedup': speedup,
                'optimization_params': data.get('params', {}),
                'stats': stats,
                'folder': folder
            }
            
            results['optimization_results'].append(result)
            
            total_kernels += 1
            if speedup > 1.0:
                successful_optimizations += 1
                speedup_ratios.append(speedup)
            
            total_baseline_time += baseline_us
            total_optimized_time += optimized_us
            
            # Process generation history for detailed optimization events
            jsonl_path = os.path.join(folder_path, 'generation_history.jsonl')
            if os.path.exists(jsonl_path):
                optimization_events = extract_optimization_events(jsonl_path, kernel_name)
                results['optimization_events'].extend(optimization_events)
                
        except (json.JSONDecodeError, FileNotFoundError) as e:
            print(f"⚠️  Error processing {json_file}: {e}")
            continue
    
    # Calculate summary statistics
    results['summary_stats'] = {
        'total_kernels': total_kernels,
        'successful_optimizations': successful_optimizations,
        'success_rate': (successful_optimizations / total_kernels * 100) if total_kernels > 0 else 0,
        'average_speedup': np.mean(speedup_ratios) if speedup_ratios else 0,
        'median_speedup': np.median(speedup_ratios) if speedup_ratios else 0,
        'max_speedup': max(speedup_ratios) if speedup_ratios else 0,
        'total_baseline_time': total_baseline_time,
        'total_optimized_time': total_optimized_time,
        'overall_speedup': (total_baseline_time / total_optimized_time) if total_optimized_time > 0 else 0
    }
    
    return results

def extract_optimization_events(jsonl_path: str, kernel_name: str) -> List[Dict[str, Any]]:
    """Extract optimization-specific events from generation history."""
    events = []
    
    try:
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    event_type = entry.get("event", "")
                    
                    # Track optimization-specific events
                    if event_type in [
                        "optimization_iteration_start",
                        "optimization_iteration_complete", 
                        "optimization_iteration_failed",
                        "sft_sample_optimization_phase1",
                        "sft_sample_optimization_hpo",
                        "early_stop_hpo",
                        "hpo_step",
                        "best_optimized_kernel_saved"
                    ]:
                        event_data = {
                            'kernel_name': kernel_name,
                            'event_type': event_type,
                            'timestamp': entry.get('ts', ''),
                            'iteration': entry.get('iter', 0),
                            'speedup': entry.get('speedup', 0),
                            'hip_us': entry.get('hip_us', 0),
                            'correct': entry.get('correct', True),
                            'params': entry.get('params', {}),
                            'reason': entry.get('reason', '')
                        }
                        events.append(event_data)
                        
                except json.JSONDecodeError:
                    continue
                    
    except FileNotFoundError:
        print(f"⚠️  Generation history not found: {jsonl_path}")
        
    return events

def generate_kernel_optimization_plots(results: Dict[str, Any], input_folder: str) -> Dict[str, str]:
    """Generate visualization plots for kernel optimization results."""
    plot_paths = {}
    
    optimization_results = results['optimization_results']
    summary_stats = results['summary_stats']
    
    if not optimization_results:
        print("⚠️  No optimization results to plot")
        return plot_paths
    
    # Set style
    plt.style.use('default')
    sns.set_palette("husl")
    
    # 1. Speedup Distribution Plot
    speedups = [r['speedup'] for r in optimization_results if r['speedup'] > 0]

    if speedups:
        fig, ax = plt.subplots(figsize=(12, 8))
        
        # Create histogram
        n, bins, patches = ax.hist(speedups, bins=20, alpha=0.7, color='skyblue', edgecolor='black')
        
        # Color bars based on speedup value
        for i, (patch, speed) in enumerate(zip(patches, bins[:-1])):
            if speed >= 2.0:
                patch.set_facecolor('green')
            elif speed >= 1.5:
                patch.set_facecolor('orange')
            elif speed >= 1.0:
                patch.set_facecolor('yellow')

            else:
                patch.set_facecolor('red')
        
        ax.axvline(x=1.0, color='red', linestyle='--', linewidth=2, label='No Improvement')
        ax.axvline(x=np.mean(speedups), color='blue', linestyle='-', linewidth=2, 
                   label=f'Average: {np.mean(speedups):.2f}x')
        
        ax.set_xlabel('Speedup Factor', fontsize=14)
        ax.set_ylabel('Number of Kernels', fontsize=14)
        ax.set_title('Kernel Optimization Speedup Distribution', fontsize=16, fontweight='bold')
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        plt.tight_layout()
        speedup_plot_path = os.path.join(input_folder, 'kernel_optimization_speedup_distribution.png')
        plt.savefig(speedup_plot_path, dpi=300, bbox_inches='tight')
        plt.close()
        plot_paths['speedup_distribution'] = speedup_plot_path
        print(f"📊 Generated speedup distribution plot: {speedup_plot_path}")
    
    # 2. Before vs After Performance Comparison
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 8))
    
    kernel_names = [r['kernel_name'][:20] + '...' if len(r['kernel_name']) > 20 else r['kernel_name'] 
                    for r in optimization_results]
    baseline_times = [r['baseline_us'] for r in optimization_results]
    optimized_times = [r['optimized_us'] for r in optimization_results]
    
    x_pos = np.arange(len(kernel_names))
    
    # Bar plot comparison
    width = 0.35
    ax1.bar(x_pos - width/2, baseline_times, width, label='Original', alpha=0.8, color='red')
    ax1.bar(x_pos + width/2, optimized_times, width, label='Optimized', alpha=0.8, color='blue')
    
    ax1.set_xlabel('Kernels', fontsize=12)
    ax1.set_ylabel('Execution Time (μs)', fontsize=12)
    ax1.set_title('Performance Comparison: Original vs Optimized', fontsize=14, fontweight='bold')
    ax1.set_xticks(x_pos)
    ax1.set_xticklabels(kernel_names, rotation=45, ha='right')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    
    # Speedup bar plot
    speedups = [r['speedup'] for r in optimization_results]
    colors = ['green' if s >= 1.0 else 'red' for s in speedups]
    
    bars = ax2.bar(x_pos, speedups, color=colors, alpha=0.7)
    ax2.axhline(y=1.0, color='black', linestyle='--', linewidth=1, label='Baseline (1x)')
    ax2.set_xlabel('Kernels', fontsize=12)
    ax2.set_ylabel('Speedup Factor', fontsize=12)
    ax2.set_title('Speedup Factor per Kernel', fontsize=14, fontweight='bold')
    ax2.set_xticks(x_pos)
    ax2.set_xticklabels(kernel_names, rotation=45, ha='right')
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    
    # Add value labels on bars
    for bar, speedup in zip(bars, speedups):
        height = bar.get_height()
        ax2.text(bar.get_x() + bar.get_width()/2., height + 0.01,
                f'{speedup:.2f}x', ha='center', va='bottom', fontsize=8)
    
    plt.tight_layout()
    comparison_plot_path = os.path.join(input_folder, 'kernel_optimization_comparison.png')
    plt.savefig(comparison_plot_path, dpi=300, bbox_inches='tight')
    plt.close()
    plot_paths['performance_comparison'] = comparison_plot_path
    print(f"📊 Generated performance comparison plot: {comparison_plot_path}")
    
    # 3. Optimization Summary Statistics
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
    
    # Success rate pie chart
    success_count = summary_stats['successful_optimizations']
    fail_count = summary_stats['total_kernels'] - success_count
    
    if success_count + fail_count > 0:
        labels = ['Improved', 'No Improvement']
        sizes = [success_count, fail_count]
        colors = ['green', 'red']
        explode = (0.05, 0)
        
        ax1.pie(sizes, explode=explode, labels=labels, colors=colors, autopct='%1.1f%%',
               shadow=True, startangle=90)
        ax1.set_title(f'Optimization Success Rate\n({success_count}/{summary_stats["total_kernels"]} kernels improved)', 
                     fontweight='bold')
    
    # Speedup statistics
    stats_labels = ['Average', 'Median', 'Maximum']
    stats_values = [summary_stats['average_speedup'], summary_stats['median_speedup'], summary_stats['max_speedup']]
    
    bars = ax2.bar(stats_labels, stats_values, color=['blue', 'orange', 'red'], alpha=0.7)
    ax2.set_ylabel('Speedup Factor', fontsize=12)
    ax2.set_title('Speedup Statistics', fontweight='bold')
    ax2.grid(True, alpha=0.3)
    
    for bar, value in zip(bars, stats_values):
        height = bar.get_height()
        ax2.text(bar.get_x() + bar.get_width()/2., height + 0.01,
                f'{value:.2f}x', ha='center', va='bottom', fontweight='bold')
    
    # Time savings
    total_baseline = summary_stats['total_baseline_time']
    total_optimized = summary_stats['total_optimized_time']
    time_saved = total_baseline - total_optimized
    
    time_labels = ['Original Total', 'Optimized Total', 'Time Saved']
    time_values = [total_baseline/1000, total_optimized/1000, time_saved/1000]  # Convert to ms
    
    ax3.bar(time_labels, time_values, color=['red', 'green', 'blue'], alpha=0.7)
    ax3.set_ylabel('Time (ms)', fontsize=12)
    ax3.set_title('Total Time Comparison', fontweight='bold')
    ax3.grid(True, alpha=0.3)
    
    # Optimization technique effectiveness (if params available)
    param_effectiveness = analyze_optimization_parameters(optimization_results)
    if param_effectiveness:
        params, avg_speedups = zip(*param_effectiveness.items())
        ax4.bar(params, avg_speedups, color='purple', alpha=0.7)
        ax4.set_ylabel('Average Speedup', fontsize=12)
        ax4.set_title('Optimization Parameter Effectiveness', fontweight='bold')
        ax4.set_xticklabels(params, rotation=45, ha='right')
        ax4.grid(True, alpha=0.3)
    else:
        ax4.text(0.5, 0.5, 'No parameter data available', ha='center', va='center', 
                transform=ax4.transAxes, fontsize=14)
        ax4.set_title('Optimization Parameters', fontweight='bold')
    
    plt.tight_layout()
    summary_plot_path = os.path.join(input_folder, 'kernel_optimization_summary.png')
    plt.savefig(summary_plot_path, dpi=300, bbox_inches='tight')
    plt.close()
    plot_paths['summary_statistics'] = summary_plot_path
    print(f"📊 Generated summary statistics plot: {summary_plot_path}")
    
    return plot_paths

def analyze_optimization_parameters(optimization_results: List[Dict]) -> Dict[str, float]:
    """Analyze which optimization parameters are most effective."""
    param_speedups = {}
    
    for result in optimization_results:
        params = result.get('optimization_params', {})
        speedup = result.get('speedup', 0)
        
        if speedup > 0:
            for param_name, param_value in params.items():
                param_key = f"{param_name}={param_value}"
                if param_key not in param_speedups:
                    param_speedups[param_key] = []
                param_speedups[param_key].append(speedup)
    
    # Calculate average speedup for each parameter combination
    param_effectiveness = {}
    for param_key, speedups in param_speedups.items():
        if len(speedups) >= 2:  # Only include parameters used multiple times
            param_effectiveness[param_key] = np.mean(speedups)
    
    # Sort by effectiveness
    return dict(sorted(param_effectiveness.items(), key=lambda x: x[1], reverse=True)[:10])

def process_kernel_optimization_mode(input_folder: str, grouping_file=None, level='level_1'):
    """Process kernel2kernel optimization results and generate reports."""
    
    # Process optimization results
    results = process_kernel_optimization_results(input_folder)
    
    if not results['optimization_results']:
        print("❌ No kernel optimization results found!")
        return
    
    print(f"✅ Processed {len(results['optimization_results'])} kernel optimizations")
    
    # Generate plots
    plot_paths = generate_kernel_optimization_plots(results, input_folder)
    
    # Generate markdown report
    report_path = generate_kernel_optimization_report(results, input_folder, plot_paths)
    
    # Update evalboard with optimization results
    config_path = os.path.join(os.path.dirname(input_folder), "config.yml") 
    if not os.path.exists(config_path):
        config_path = "config.yml"
    config = load_config(config_path)
    
    # Create summary stats compatible with evalboard
    summary_stats = results['summary_stats']
    evalboard_stats = {
        'total_kernels': summary_stats['total_kernels'],
        'successful_kernels': summary_stats['successful_optimizations'], 
        'avg_torch_time': summary_stats['total_baseline_time'] / summary_stats['total_kernels'] if summary_stats['total_kernels'] > 0 else 0,
        'avg_hip_time': summary_stats['total_optimized_time'] / summary_stats['total_kernels'] if summary_stats['total_kernels'] > 0 else 0,
        'overall_speedup': summary_stats['overall_speedup'],
        'success_rate': summary_stats['success_rate']
    }
    
    # Copy plots to reports directory
    main_folder = os.path.dirname(os.path.abspath(input_folder))
    if main_folder.endswith('/kernel-agentic'):
        main_folder = os.path.dirname(main_folder)
    
    # Use folder name or generate run name
    run_name = os.path.basename(input_folder) or f"kernel_optimization_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    copied_paths = copy_plots_to_reports(run_name, input_folder, main_folder, config)
    
    update_evalboard(run_name, config, evalboard_stats, input_folder, main_folder, mode='kernel2kernel')
    
    print(f"\n📊 Kernel optimization analysis complete!")
    print(f"📈 Success rate: {summary_stats['success_rate']:.1f}% ({summary_stats['successful_optimizations']}/{summary_stats['total_kernels']} kernels)")
    print(f"🚀 Average speedup: {summary_stats['average_speedup']:.2f}x")
    print(f"🏆 Maximum speedup: {summary_stats['max_speedup']:.2f}x")
    print(f"📁 Results saved to: {os.path.join(main_folder, 'reports', run_name)}")
    
    return results

def generate_kernel_optimization_report(results: Dict[str, Any], input_folder: str, plot_paths: Dict[str, str]) -> str:
    """Generate a comprehensive markdown report for kernel optimization results."""
    
    summary_stats = results['summary_stats']
    optimization_results = results['optimization_results']
    
    # Generate report content
    report_content = f"""# Kernel Optimization Analysis Report

## 📊 Summary Statistics

- **Total Kernels Analyzed**: {summary_stats['total_kernels']}
- **Successfully Optimized**: {summary_stats['successful_optimizations']} ({summary_stats['success_rate']:.1f}%)
- **Average Speedup**: {summary_stats['average_speedup']:.2f}x
- **Median Speedup**: {summary_stats['median_speedup']:.2f}x
- **Maximum Speedup**: {summary_stats['max_speedup']:.2f}x
- **Overall Performance Improvement**: {summary_stats['overall_speedup']:.2f}x

## 📈 Performance Analysis

### Time Comparison
- **Total Original Time**: {summary_stats['total_baseline_time']:.1f} μs
- **Total Optimized Time**: {summary_stats['total_optimized_time']:.1f} μs
- **Time Saved**: {summary_stats['total_baseline_time'] - summary_stats['total_optimized_time']:.1f} μs

## 🎯 Individual Kernel Results

| Kernel Name | Original (μs) | Optimized (μs) | Speedup | Status |
|-------------|---------------|----------------|---------|---------|
"""
    
    # Add individual results
    for result in optimization_results:
        status = "✅ Improved" if result['speedup'] > 1.0 else "❌ No improvement"
        report_content += f"| {result['kernel_name']} | {result['baseline_us']:.1f} | {result['optimized_us']:.1f} | {result['speedup']:.2f}x | {status} |\n"
    
    # Add optimization techniques section if available
    if optimization_results and any(r.get('optimization_params') for r in optimization_results):
        report_content += "\n## 🔧 Optimization Techniques Applied\n\n"
        
        for result in optimization_results:
            params = result.get('optimization_params', {})
            if params:
                report_content += f"### {result['kernel_name']}\n"
                report_content += f"- **Speedup achieved**: {result['speedup']:.2f}x\n"
                report_content += "- **Parameters optimized**:\n"
                for param, value in params.items():
                    report_content += f"  - `{param}`: {value}\n"
                report_content += "\n"
    
    # # Add plots section
    # if plot_paths:
    #     report_content += "\n## 📊 Visualization Charts\n\n"
    #     for plot_name, plot_path in plot_paths.items():
    #         plot_filename = os.path.basename(plot_path)
    #         report_content += f"### {plot_name.replace('_', ' ').title()}\n"
    #         report_content += f"![{plot_name}]({plot_filename})\n\n"
    
    # Add methodology section
    report_content += """
## 🔬 Optimization Methodology

This analysis used the kernel2kernel optimization pipeline, which:

1. **Analyzes existing kernels** to understand their computational patterns and identify bottlenecks
2. **Applies targeted optimizations** using LLM-guided code improvements
3. **Iteratively refines** kernels based on profiling feedback
4. **Performs hyperparameter optimization** to fine-tune kernel parameters

The optimization process focuses on:
- Memory access pattern improvements
- Compute utilization optimization  
- Register usage optimization
- Shared memory optimization
- Architecture-specific optimizations

## 📝 Notes

- Speedup is calculated as: `Original Time / Optimized Time`
- Results include both algorithmic improvements and parameter tuning
- All timings are in microseconds (μs)
- Optimizations maintain functional correctness while improving performance
"""
    
    # Generate timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_content += f"\n---\n*Report generated on {timestamp}*\n"
    
    # Save report
    report_path = os.path.join(input_folder, "kernel_optimization_report.md")
    with open(report_path, 'w') as f:
        f.write(report_content)
    
    print(f"📄 Generated optimization report: {report_path}")
    return report_path

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Post-process HIPKernelBench results')
    parser.add_argument('--input', '-i', required=True, help='Input directory containing results')
    parser.add_argument('--grouping', '-g', help='Path to grouping.yml file')
    parser.add_argument('--level', '-l', default='level_1', help='Level to process (default: level_1)')
    parser.add_argument('--run-name', '-r', help='Custom run name for evalboard')
    
    args = parser.parse_args()
    
    if args.run_name:
        # Custom run name mode - update evalboard with custom name
        print(f"🚀 Processing with custom run name: {args.run_name}")
        
        original_input = args.input
        main(original_input, args.grouping, args.level)
        
        # After processing, update the evalboard with custom name
        config_path = os.path.join(os.path.dirname(original_input), "config.yml")
        if not os.path.exists(config_path):
            config_path = "config.yml"
        config = load_config(config_path)
        
        # Determine mode based on directory structure
        run_mode = detect_run_mode(original_input)
        
        list_baselines_flat = []
        list_best_flat = []
        
        # Quick recalculation for evalboard
        for folder in os.listdir(original_input):
            if not os.path.isdir(os.path.join(original_input, folder)):
                continue
            
            if run_mode == 'kernel2kernel':
                # For kernel optimization, look for optimized.json files
                if folder.endswith('_opt'):
                    json_files = [f for f in os.listdir(os.path.join(original_input, folder)) if f.endswith('_optimized.json')]
                    if json_files:
                        json_path = os.path.join(original_input, folder, json_files[0])
                        try:
                            with open(json_path, 'r') as f:
                                data = json.load(f)
                            baseline_time = data.get('baseline_us', 0) or 0
                            optimized_time = data.get('stats', {}).get('avg_us', baseline_time) or baseline_time
                            
                            # Ensure values are numeric
                            baseline_time = float(baseline_time) if baseline_time is not None else 0.0
                            optimized_time = float(optimized_time) if optimized_time is not None else baseline_time
                            
                            list_baselines_flat.append(baseline_time)
                            list_best_flat.append(optimized_time)
                        except (json.JSONDecodeError, FileNotFoundError):
                            continue
            else:
                # For pytorch2kernel, look for baseline files
                baseline_files = [f for f in os.listdir(os.path.join(original_input, folder)) if f.startswith("baseline") and f.endswith('.json')]
                if not baseline_files:
                    continue
                    
                with open(os.path.join(original_input, folder, baseline_files[0]), 'r') as f:
                    data = json.load(f)
                baseline_time = data['lat_us']
                list_baselines_flat.append(baseline_time)
                
                jsonl_path = os.path.join(original_input, folder, "generation_history.jsonl")
                best_hip_time = baseline_time  # Default to baseline
                
                if os.path.isfile(jsonl_path):
                    smallest_hip_us = None
                    with open(jsonl_path, 'r', encoding='utf-8') as f:
                        for line in f:
                            try:
                                entry = json.loads(line)
                                event = entry.get("event", "")
                                if event in ["sft_sample_phase1", "sft_sample_hpo"]:
                                    current_hip_us = entry.get("hip_us")
                                    if current_hip_us is not None:
                                        if smallest_hip_us is None or current_hip_us < smallest_hip_us:
                                            smallest_hip_us = current_hip_us
                            except json.JSONDecodeError:
                                continue
                    
                    if smallest_hip_us is not None:
                        best_hip_time = smallest_hip_us
                        
                list_best_flat.append(best_hip_time)
        
        stats = generate_summary_stats(list_baselines_flat, list_best_flat)
        main_folder = os.path.dirname(os.path.abspath(original_input))
        if main_folder.endswith('/kernel-agentic'):
            main_folder = os.path.dirname(main_folder)
        
        update_evalboard(args.run_name, config, stats, original_input, main_folder, mode=run_mode)
        
        print(f"\n  Custom run name '{args.run_name}' used in evalboard (Mode: {run_mode})")
        print(f" Performance charts stored in: {os.path.join(main_folder, 'reports', args.run_name)}")
    else:
        main(input=args.input, grouping_file=args.grouping, level=args.level)
