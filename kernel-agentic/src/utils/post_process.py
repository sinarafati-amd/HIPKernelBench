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

level_1 ={
    'group1_matrix_multiplication': ['4D_tensor_matrix_multiplication', '3D_tensor_matrix_multiplication',
                                    'Square_matrix_multiplication_', 'Standard_matrix_multiplication_',
                                    'Matrix_vector_multiplication_', 'Matrix_scalar_multiplication',
                                    'Batched_matrix_multiplication', 'Matmul_with_irregular_shapes_',
                                    'Matmul_with_small_K_dimension_', 'Matmul_with_large_K_dimension_',
                                    'Matmul_with_diagonal_matrices_', 'Matmul_for_symmetric_matrices',
                                    'Matmul_for_upper_triangular_matrices', 'Matmul_for_lower_triangular_matrices',
                                    'Matmul_with_transposed_A', 'Matmul_with_transposed_B', 'Matmul_with_transposed_both'
                                    ],
    'group2_cumsum_related' :       ['cumsum', 'cumprod', 'cumsum_reverse', 'cumsum_exclusive', 'masked_cumsum'],
    'group3_loss_functions':       ['HingeLoss', 'MSELoss', 'CrossEntropyLoss', 'HuberLoss',
                                        'CosineSimilarityLoss', 'KLDivLoss', 'TripletMarginLoss'
                                    ],

    'group4_convolutions':          ['conv_transposed_1D_asymmetric_input_square_kernel___padded____strided____dilated__',
                                    'conv_transposed_1D_dilated', 'conv_transposed_1D',
                                    'conv_transposed_2D__asymmetric_input__asymmetric_kernel',
                                    'conv_transposed_2D__asymmetric_input__square_kernel',
                                    'conv_transposed_2D__square_input__square_kernel',
                                    'conv_transposed_2D__square_input__asymmetric_kernel',
                                    'conv_transposed_2D_asymmetric_input_square_kernel___dilated____padded____strided__',
                                    'conv_transposed_2D_asymmetric_input_asymmetric_kernel___padded__',
                                    'conv_transposed_2D_asymmetric_input_asymmetric_kernel_strided__grouped____padded____dilated__',
                                    'conv_transposed_3D__square_input__asymmetric_kernel',
                                    'conv_transposed_3D__asymmetric_input__square_kernel',
                                    'conv_transposed_3D__asymmetric_input__asymmetric_kernel',
                                    'conv_transposed_3D__square_input__square_kernel',
                                    'conv_transposed_3D_asymmetric_input_square_kernel__strided_padded__grouped',
                                    'conv_transposed_3D_asymmetric_input_asymmetric_kernel___strided_padded_grouped_',
                                    'conv_standard_1D', 'conv_standard_1D_dilated_strided__',
                                    'conv_standard_2D_square_input_asymmetric_kernel___dilated____padded__',
                                    'conv_standard_2D__asymmetric_input__asymmetric_kernel',
                                    'conv_standard_2D__asymmetric_input__square_kernel',
                                    'conv_standard_2D__square_input__asymmetric_kernel',
                                    'conv_standard_2D__square_input__square_kernel',
                                    'conv_standard_3D__asymmetric_input__square_kernel',
                                    'conv_standard_3D__square_input__asymmetric_kernel',
                                    'conv_standard_3D__asymmetric_input__asymmetric_kernel',
                                    'conv_standard_3D__square_input__square_kernel'
                                ],
    'group5_special_convs':          ['conv_depthwise_2D_square_input_square_kernel',
                                    'conv_depthwise_2D_square_input_asymmetric_kernel',
                                    'conv_depthwise_2D_asymmetric_input_square_kernel',
                                    'conv_depthwise_2D_asymmetric_input_asymmetric_kernel',
                                    'conv_depthwise_separable_2D',
                                    'conv_pointwise_2D'
                                    ],
    'group6_pooling':               ['Max_Pooling_1D', 'Max_Pooling_2D', 'Max_Pooling_3D',
                                        'Average_Pooling_1D', 'Average_Pooling_2D', 'Average_Pooling_3D'
                                    ],

    'group7_reductions':            ['Sum_reduction_over_a_dimension', 'Mean_reduction_over_a_dimension',
                                        'Max_reduction_over_a_dimension', 'Min_reduction_over_a_dimension',
                                        'Product_reduction_over_a_dimension', 'Argmax_over_a_dimension',
                                        'Argmin_over_a_dimension'
                                    ],
    'group8_normalization':         ['BatchNorm', 'InstanceNorm', 'GroupNorm_', 'RMSNorm_',
                                        'LayerNorm', 'FrobeniusNorm_', 'L1Norm_', 'L2Norm_'
                                    ],
    'group9_activations':           ['ReLU', 'LeakyReLU', 'ELU', 'SELU_', 'Swish', 'GELU_',
                                        'MinGPTNewGelu', 'HardTanh', 'HardSigmoid', 'Sigmoid', 'Tanh',
                                        'Softmax', 'LogSoftmax', 'Softplus', 'Softsign'
                                    ]   
}


def main(input):
    # Create a mapping from group names to indices
    group_name_to_index = {group: idx for idx, group in enumerate(level_1.keys())}

    # Initialize list_baselines with one empty list per group
    list_baselines = [[] for _ in range(len(level_1))]
    list_best = [[] for _ in range(len(level_1))]

    folders = os.listdir(input)

    for folder in folders:
        if folder.endswith('.jsonl') or folder.endswith('.png'):
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
        for group, names in level_1.items():
            if name in names:
                group_index = group_name_to_index[group]
                list_baselines[group_index].append(baseline_time)
                list_best[group_index].append(baseline_time if best_sft_entry is None else best_sft_entry['hip_us'])
                break
        else:
            print(f"Name {name} not found in any group")


    # Step 1: Calculate average baseline time per group
    group_names = list(level_1.keys())
    average_times = [np.mean(times) if times else 0 for times in list_baselines]
    average_best_times = [np.mean(times) if times else 0 for times in list_best]

    breakpoint()

    # Step 2: Remove 'groupX_' prefix from group names
    cleaned_names = [name.split('_', 1)[-1] if '_' in name else name for name in group_names]

    fig, ax = plt.subplots(figsize=(16, 6))
    sns.barplot(x=cleaned_names, y=average_times,    label='Torch', ax=ax)
    sns.barplot(x=cleaned_names, y=average_best_times, label='HIP',
                alpha=0.7, ax=ax)

    ax.set_yscale('log')
    ax.set_ylabel('Average Time (μs) [log scale]')
    ax.grid(axis='y', which='both', linestyle='--', linewidth=0.5)
    plt.tight_layout()
    plt.savefig(os.path.join(input, 'average_baseline_barplot_log.png'))
    plt.show()

    #flatten lsit of list
    list_baselines_flat = [item for sublist in list_baselines for item in sublist]
    list_best_flat = [item for sublist in list_best for item in sublist]

    # prepare a common x-grid
    plt.figure(figsize=(10,6))

    sns.kdeplot(
        list_baselines_flat,
        fill=True, alpha=0.15,
        label='Torch',
        # don’t extend beyond your data
        cut=0,
        # hard‐clip at 0 on the left
        clip=(0, None),
        # if the default bandwith is too smooth, shrink it
        bw_adjust=0.8,
        common_norm=False
    )

    sns.kdeplot(
        list_best_flat,
        fill=True, alpha=0.15,
        label='HIP',
        color='orange',
        cut=0,
        clip=(0, None),
        bw_adjust=0.8,
        common_norm=False
    )

    plt.xlim(0, None)            # kill any negative x-axis
    plt.xlabel('Time (μs)')
    plt.ylabel('Density (area=1)')
    plt.title('Density Plot (clipped + properly normalized)')
    plt.grid(axis='y', linestyle='--', alpha=0.5)
    plt.legend(loc='upper right')
    plt.tight_layout()
    plt.savefig(os.path.join(input, 'baseline_density_cleaned.png'))
    plt.show()

    

if __name__ == "__main__":
    parser = ArgumentParser(description="Post-process the generated kernels.")
    parser.add_argument("--input", type=str, default='logs', help="Path to the input file.")
    args = parser.parse_args()

    main(input=args.input)
    