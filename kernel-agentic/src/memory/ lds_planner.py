from ..utils.gpu_specs import get_gpu_specs
def fits(tile_smem_bytes):
    return tile_smem_bytes <= get_gpu_specs()["shared_mem"]