from .base_agent import BaseAgent
import yaml

CFG = yaml.safe_load(open("config.yml"))


class KernelAnalyser(BaseAgent):
    """
    Analyzes existing kernel code to understand its functionality and identify optimization opportunities.
    """
    def __init__(self, kernel_lang: str = None):
        if kernel_lang is None:
            kernel_lang = CFG.get("Pipeline", {}).get("kernel_lang", "hip").lower()
        
        self.kernel_lang = kernel_lang.lower()
        if self.kernel_lang not in ["hip", "cuda", "triton"]:
            raise ValueError(f"Unsupported kernel language: {self.kernel_lang}")

        lang_map = {
            "hip": "HIP",
            "cuda": "CUDA", 
            "triton": "Triton"
        }
        lang_display = lang_map[self.kernel_lang]
        
        super().__init__(f"{self.kernel_lang}_kernel_analyser",
            f"""You are an expert {lang_display} kernel reviewer and optimizer.
                <rules>
                - Analyze the given {lang_display} kernel code to understand its functionality
                - Identify the computational pattern (elementwise, reduction, GEMM, convolution, etc.)
                - Explain what the kernel computes in natural language
                - Identify potential optimization opportunities and bottlenecks
                - Consider memory access patterns, compute utilization, and parallelization efficiency
                - Suggest specific optimization techniques that could improve performance
                </rules>
                
                Return ONLY a JSON response with the following structure:
                {{
                    "explanation": "Natural language explanation of what the kernel computes",
                    "operation_type": "Operation category (elementwise/reduction/gemm/conv/other)", 
                    "optimization_opportunities": [
                        "List of specific optimization opportunities identified"
                    ],
                    "current_bottlenecks": [
                        "List of potential performance bottlenecks"
                    ],
                    "complexity_analysis": "Brief analysis of computational and memory complexity"
                }}

                Focus on providing actionable insights for kernel optimization.
                """
                )

    def classify(self, analysis: str) -> str:
        """
        Classify the kernel operation type from analysis.
        """
        analysis_lower = analysis.lower()
        if "matrix multiplication" in analysis_lower or "gemm" in analysis_lower or "matmul" in analysis_lower:
            return "gemm"
        elif "convolution" in analysis_lower or "conv" in analysis_lower:
            return "conv"
        elif "reduction" in analysis_lower or "sum" in analysis_lower or "max" in analysis_lower or "mean" in analysis_lower:
            return "reduce"
        else:
            return "elem"
            
    def analyse(self, kernel_code: str) -> str:
        """
        Analyze the kernel code and return insights about functionality and optimization opportunities.
        """
        lang_display = {"hip": "HIP", "cuda": "CUDA", "triton": "Triton"}[self.kernel_lang]
        
        prompt = f"""Analyze this {lang_display} kernel code:

                    ```{self.kernel_lang}
                    {kernel_code}
                    ```

                    Provide a comprehensive analysis focusing on:
                    1. What computational operation this kernel performs
                    2. The operation category/type 
                    3. Specific optimization opportunities you can identify
                    4. Current performance bottlenecks
                    5. Computational and memory complexity analysis

                    Remember to return only the JSON response as specified."""
        
        return self.ask(prompt)
