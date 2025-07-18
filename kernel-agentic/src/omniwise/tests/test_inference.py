# SPDX-License-Identifier: MIT
# Copyright (c) 2024 Advanced Micro Devices, Inc. All rights reserved.

import base64
import json
import requests

# Define the server URL
SERVER_URL = "http://localhost:8081"

# Define the C++ code
CODE = """
#include <hip/hip_runtime.h>
#include <iostream>

// Kernel to perform vector addition
__global__ void vector_add(const float* A, const float* B, float* C, size_t N) {
    size_t idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx < N) {
        C[idx] = A[idx] + B[idx];
    }
}

int main() {
    const size_t N = 1024; // Size of the vectors
    const size_t bytes = N * sizeof(float);

    // Host vectors
    float *h_A = new float[N];
    float *h_B = new float[N];
    float *h_C = new float[N];

    // Initialize input vectors
    for (size_t i = 0; i < N; ++i) {
        h_A[i] = static_cast<float>(i);
        h_B[i] = static_cast<float>(N - i);
    }

    // Device vectors
    float *d_A, *d_B, *d_C;
    hipMalloc(&d_A, bytes);
    hipMalloc(&d_B, bytes);
    hipMalloc(&d_C, bytes);

    // Copy data to device
    hipMemcpy(d_A, h_A, bytes, hipMemcpyHostToDevice);
    hipMemcpy(d_B, h_B, bytes, hipMemcpyHostToDevice);

    // Launch kernel
    const size_t threads_per_block = 256;
    const size_t blocks_per_grid = (N + threads_per_block - 1) / threads_per_block;

    hipLaunchKernelGGL(vector_add, dim3(blocks_per_grid), dim3(threads_per_block), 0, 0, d_A, d_B, d_C, N);

    // Copy result back to host
    hipMemcpy(h_C, d_C, bytes, hipMemcpyDeviceToHost);

    // Verify results
    for (size_t i = 0; i < N; ++i) {
        if (h_C[i] != h_A[i] + h_B[i]) {
            std::cerr << "Error at index " << i << ": " << h_C[i] << " != " << h_A[i] + h_B[i] << std::endl;
            return -1;
        }
    }

    std::cout << "Vector addition completed successfully!" << std::endl;

    // Cleanup
    delete[] h_A;
    delete[] h_B;
    delete[] h_C;
    hipFree(d_A);
    hipFree(d_B);
    hipFree(d_C);

    return 0;
}
"""

# Encode the C++ code in Base64
encoded_code = base64.b64encode(CODE.encode("utf-8")).decode("utf-8")

# Create the JSON payload
JSON_PAYLOAD = {"architecture": "gfx90a", "compiler_flags": "-O3", "code": encoded_code}

# Convert the payload to a JSON string
json_payload_str = json.dumps(JSON_PAYLOAD)

# Print the payload for debugging
print(f"Posting: {json_payload_str}")

# Send the POST request
response = requests.post(
    SERVER_URL, headers={"Content-Type": "application/json"}, data=json_payload_str
)

# Print the server response
print(f"Response: {response.status_code}")
print(f"Response Body: {response.text}")
