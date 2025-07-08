#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2024 Advanced Micro Devices, Inc. All rights reserved.

ENV_NAME="omniwise"

if [ ! -d "$ENV_NAME" ]; then
    python3 -m venv $ENV_NAME
fi

source $ENV_NAME/bin/activate
pip install -r requirements.txt
