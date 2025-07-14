import subprocess, yaml, tempfile, os, textwrap
from ..utils.kernel_compiler import compile_kernel
from ..utils.rocprof_parser import profile
from ..utils.logger import log
from typing import Optional, Tuple, Dict




class Executor:
    def __init__(self, kernel_lang: str = None):
        import yaml
        CFG = yaml.safe_load(open("config.yml"))
        if kernel_lang is None:
            kernel_lang = CFG.get("Pipeline", {}).get("kernel_lang", "hip")
        self.kernel_lang = kernel_lang.lower()
    
    def run(self, kernel_code: str) -> Tuple[Optional[Dict], Optional[str]]:
        
        bin_path, stdout, stderr, kernel_file = compile_kernel(kernel_code, self.kernel_lang)
        # if there was a compilation/runtime error, return no stats and the stderr
        if stderr:
            log.append({
                "event": "execution_error",
                "stderr": stderr.strip()
            })
            return None, stderr, kernel_file
        # otherwise profile the binary
        stats = profile(bin_path)
        log.append({
            "event": "execution_stats",
            **stats
        })
        return stats, None, kernel_file


