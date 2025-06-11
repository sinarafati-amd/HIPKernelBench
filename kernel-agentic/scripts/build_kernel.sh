#!/usr/bin/env bash
hipcc --amdgpu-target=${1:-gfx1100} kernel.hip -o kernel.out
