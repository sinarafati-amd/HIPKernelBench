import math, yaml
from gpu_specs import get_gpu_specs

def suggest_launch(tile_smem_bytes: int, threads_per_block: int):
    spec = get_gpu_specs()
    print(spec)
    max_smem = spec["shared_mem"]
    max_thr  = 1024

    # fit shared mem
    blocks_per_sm_sm = max_smem // tile_smem_bytes
    blocks_per_sm_thr = max_thr // threads_per_block
    blocks_per_sm = max(1, min(blocks_per_sm_sm, blocks_per_sm_thr))

    return {
        "threads_per_block": threads_per_block,
        "blocks_per_sm": blocks_per_sm,
        "grid_size": blocks_per_sm * spec["cu_count"]
    }


tile=4
tpb=64
launch   = suggest_launch(tile*tile*4, tpb)
print(launch)