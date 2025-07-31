#!/bin/bash
set -e

sudo apt-get install locales
sudo locale-gen en_US.UTF-8
sudo update-locale LANG=en_US.UTF-8


# Set hf 
echo "[INFO] Please enter your Hugging Face token:"
read -s _TOKEN
echo ""

if [ -z "$_TOKEN" ]; then
    echo "[ERROR] No token provided. Skipping Hugging Face setup."
    _TOKEN=""
else
    echo "[INFO] Token received successfully."
fi

# Check if wget is installed, if not, install it
if ! command -v wget &> /dev/null; then
    echo "[INFO] wget not installed, installing now..."
    
    # Try to install wget using package manager
    if command -v apt-get &> /dev/null; then
        sudo apt-get update && sudo apt-get install -y wget
    elif command -v yum &> /dev/null; then
        sudo yum install -y wget
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y wget
    else
        echo "[ERROR] Could not find package manager to install wget. Please install wget manually."
        exit 1
    fi
    
    if ! command -v wget &> /dev/null; then
        echo "[ERROR] wget installation failed. Please install wget manually."
        exit 1
    fi
fi
echo "[INFO] wget installed, version: $(wget --version | head -n1)"

# Check if tmux is installed, if not, install it
if ! command -v tmux &> /dev/null; then
    echo "[INFO] tmux not installed, installing now..."
    
    # Try to install tmux using package manager
    if command -v apt-get &> /dev/null; then
        sudo apt-get update && sudo apt-get install -y tmux
    elif command -v yum &> /dev/null; then
        sudo yum install -y tmux
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y tmux
    else
        echo "[ERROR] Could not find package manager to install tmux. Please install tmux manually."
        exit 1
    fi
    
    if ! command -v tmux &> /dev/null; then
        echo "[ERROR] tmux installation failed. Please install tmux manually."
        exit 1
    fi
fi
echo "[INFO] tmux installed, version: $(tmux -V)"

# Check if yq is installed, if not, install it
if ! command -v yq &> /dev/null; then
    echo "[INFO] yq not installed, installing now..."
    
    # Install yq using the recommended method
    sudo wget -qO /usr/local/bin/yq https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64
    sudo chmod +x /usr/local/bin/yq
    
    if ! command -v yq &> /dev/null; then
        echo "[ERROR] yq installation failed. Please install yq manually."
        exit 1
    fi
fi
echo "[INFO] yq installed, version: $(yq --version)"

# Check if expect is installed, if not, install it
if ! command -v expect &> /dev/null; then
    echo "[INFO] expect not installed, installing now..."
    
    # Try to install expect using package manager
    if command -v apt-get &> /dev/null; then
        sudo apt-get update && sudo apt-get install -y expect
    elif command -v yum &> /dev/null; then
        sudo yum install -y expect
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y expect
    else
        echo "[ERROR] Could not find package manager to install expect. Please install expect manually."
        exit 1
    fi
    
    if ! command -v expect &> /dev/null; then
        echo "[ERROR] expect installation failed. Please install expect manually."
        exit 1
    fi
fi
echo "[INFO] expect installed, version: $(expect -version)"

# Check if cmake is installed, if not, install it
if ! command -v cmake &> /dev/null; then
    echo "[INFO] cmake not installed, installing now..."
    
    # Try to install cmake using package manager
    if command -v apt-get &> /dev/null; then
        sudo apt-get update && sudo apt-get install -y cmake
    elif command -v yum &> /dev/null; then
        sudo yum install -y cmake
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y cmake
    else
        echo "[ERROR] Could not find package manager to install cmake. Please install cmake manually."
        exit 1
    fi
    
    if ! command -v cmake &> /dev/null; then
        echo "[ERROR] cmake installation failed. Please install cmake manually."
        exit 1
    fi
fi
echo "[INFO] cmake installed, version: $(cmake --version | head -n1)"


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

    source .venv/bin/activate
    # Install ROCm Support PyTorch, and other dependencies
    echo "[INFO] Installing PyTorch ROCm dependencies..."
    uv pip install --index-url https://download.pytorch.org/whl/rocm6.3 -r pytorch_rocm.txt --no-cache-dir

    echo "[INFO] Installing general project requirements..."
    uv pip install -r requirements.txt --no-cache-dir

    echo "[INFO] Development environment setup complete."

else
    echo "[INFO] .venv already exists, skipping creation."
    uv pip install -r requirements.txt --no-cache-dir
    uv pip install --index-url https://download.pytorch.org/whl/rocm6.3 -r pytorch_rocm.txt --no-cache-dir
fi


source .venv/bin/activate


echo "[INFO] Updating config.yml with Hugging Face token..."
if [ -f "config.yml" ]; then
    export HF_TOKEN="$_TOKEN"
    yq eval '.training.lora.huggingface_read = strenv(HF_TOKEN)' -i config.yml
    echo "[INFO] Successfully updated config.yml with Hugging Face token"
else
    echo "[WARNING] config.yml not found, skipping token update"
fi

# Use expect to automate hf auth login
echo "[INFO] Logging into Hugging Face CLI..."
expect << EOF
spawn hf auth login
expect "Enter your token (input will not be visible):"
send "$_TOKEN\r"
expect "Add token as git credential? (Y/n)"
send "n\r"
expect eof
EOF


# Check if tmux session "omniwise_session" exists
echo "[INFO] Checking for existing tmux session 'omniwise_session'..."
if tmux has-session -t omniwise_session 2>/dev/null; then
    echo "[INFO] tmux session 'omniwise_session' already exists, skipping creation"
else
    echo "[INFO] Creating new tmux session 'omniwise_session' and starting omniwise server..."
    tmux new-session -d -s omniwise_session -c "$PWD" "source .venv/bin/activate && make omniwise-server"
    echo "[INFO] tmux session 'omniwise_session' created and omniwise server started"
    echo "[INFO] To attach to the session, run: tmux attach-session -t omniwise_session"
fi



# Get the current working directory
CUR_DIR=$(pwd)

# Find the python path inside the .venv
VENV_PYTHON="$CUR_DIR/.venv/bin/python"

# Check if rocprof-compute exists before creating alias
if [ -f "/opt/rocm/bin/rocprof-compute" ]; then
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
else
    echo "[INFO] /opt/rocm/bin/rocprof-compute not found, installing rocprofiler-compute..."
    
    # Check if ROCm is installed (minimum version 6.2.0 required)
    if command -v apt-get &> /dev/null; then
        # Install rocprofiler-compute via package manager
        echo "[INFO] Installing rocprofiler-compute via apt..."
        sudo apt update && sudo apt install -y rocprofiler-compute
        
        # Install Python dependencies for rocprofiler-compute
        if [ -f "/opt/rocm/libexec/rocprofiler-compute/requirements.txt" ]; then
            echo "[INFO] Installing rocprofiler-compute Python dependencies..."
            uv pip install -r /opt/rocm/libexec/rocprofiler-compute/requirements.txt --no-cache-dir
        fi
    else
        echo "[WARNING] Could not find package manager to install rocprofiler-compute."
        echo "[INFO] Please install ROCm (>=6.2.0) and rocprofiler-compute manually."
        echo "[INFO] See: https://rocm.docs.amd.com/projects/rocprofiler-compute/en/latest/install/core-install.html"
    fi
    
    # Check again if rocprof-compute is now available and create alias
    if [ -f "/opt/rocm/bin/rocprof-compute" ]; then
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
        
        # Add rocprof-compute to system PATH
        sudo update-alternatives --install /usr/bin/rocprof-compute rocprof-compute /opt/rocm/bin/rocprof-compute 0 2>/dev/null || true
        
        echo "[INFO] rocprofiler-compute installation completed successfully"
    else
        echo "[WARNING] rocprofiler-compute installation failed or rocprof-compute not found"
    fi
fi

echo "Run 'source .venv/bin/activate' to activate the virtual environment."
