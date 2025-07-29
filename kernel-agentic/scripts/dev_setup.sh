#!/bin/bash
set -e

# Check if uv is installed, if not, install it
if ! command -v uv &> /dev/null; then
    echo "[INFO] uv not install, install now..."

    curl -LsSf https://astral.sh/uv/install.sh | sh
    source $HOME/.local/bin/env

    if ! command -v uv &> /dev/null; then
        echo "[ERROR] uv installation failed. Please install uv manually."
        exit 1
    fi
fi
echo "[INFO] uv installed, version: $(uv --version)"

# Sets up the development environment for the kernel-agentic project.
if [ ! -d ".venv" ]; then
    uv venv .venv
else
    echo "[INFO] .venv already exists, skipping creation."
fi
source .venv/bin/activate

# Install ROCm Support PyTorch, and other dependencies
echo "[INFO] Installing PyTorch ROCm dependencies..."
uv pip install --index-url https://download.pytorch.org/whl/rocm6.3 -r pytorch_rocm.txt --no-cache-dir

echo "[INFO] Installing general project requirements..."
uv pip install -r requirements.txt --no-cache-dir

echo "[INFO] Development environment setup complete."




# Get the current working directory
CUR_DIR=$(pwd)

# Find the python path inside the .venv
VENV_PYTHON="$CUR_DIR/.venv/bin/python"

alias rocprof-compute="$VENV_PYTHON /opt/rocm/bin/rocprof-compute"

# Prepare the alias command with the venv python
ALIAS_CMD="alias rocprof-compute=\"$VENV_PYTHON /opt/rocm/bin/rocprof-compute\""

# Check if the alias already exists in .bashrc to avoid duplicates
if ! grep -Fxq "$ALIAS_CMD" ~/.bashrc; then
    echo "$ALIAS_CMD" >> ~/.bashrc
    echo "[INFO] Added rocprof-compute alias to ~/.bashrc"
else
    echo "[INFO] rocprof-compute alias already exists in ~/.bashrc"
fi

echo "Run 'source .venv/bin/activate' to activate the virtual environment."