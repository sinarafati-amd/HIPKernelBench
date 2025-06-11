import subprocess, yaml, tempfile, os, textwrap
from ..utils.hip_compiler import compile_hip
from ..utils.rocprof_parser import profile
from ..utils.logger import log
from typing import Optional, Tuple, Dict




class Executor:
    def run(self, hip_code: str) -> Tuple[Optional[Dict], Optional[str]]:
        
        bin_path, stdout, stderr, hip_file = compile_hip(hip_code)
        # if there was a compilation/runtime error, return no stats and the stderr
        if stderr:
            log.append({
                "event": "execution_error",
                "stderr": stderr.strip()
            })
            return None, stderr, hip_file
        # otherwise profile the binary
        stats = profile(bin_path)
        log.append({
            "event": "execution_stats",
            **stats
        })
        return stats, None, hip_file


