#!/bin/bash
set -e

# Check if uv is installed, if not, install it
if ! command -v uv &> /dev/null; then
    echo "[INFO] uv not install, install now..."

    curl -LsSf https://astral.sh/uv/install.sh | sh

    if ! command -v uv &> /dev/null; then
        echo "[ERROR] uv installation failed. Please install uv manually."
        exit 1
    fi
fi
echo "[INFO] uv installed, version: $(uv --version)"

# Sets up the development environment for the kernel-agentic project.
uv venv .venv
source .venv/bin/activate

# Install ROCm Support PyTorch, and other dependencies
echo "[INFO] Installing PyTorch ROCm dependencies..."
uv pip install --index-url https://download.pytorch.org/whl/rocm6.3 -r pytorch_rocm.txt --no-cache-dir

echo "[INFO] Installing general project requirements..."
uv pip install -r requirements.txt --no-cache-dir

echo "[INFO] Development environment setup complete."