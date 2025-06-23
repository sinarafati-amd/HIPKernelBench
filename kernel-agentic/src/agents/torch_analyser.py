from .base_agent import BaseAgent


class TorchAnalyser(BaseAgent):
    def __init__(self):
        super().__init__("torch_analyser",
            """You are an expert PyTorch reviewer.
                <rules>
                - Produce a concise but complete natural-language explanation of what the given code computes.
                - Include tensor shapes, computational steps, and implicit broadcasting.
                </rules>
                Return ONLY the explanation.""")

    def classify(self, expl: str) -> str:
        if "matrix multiplication" in expl or "GEMM" in expl:
            return "gemm"
        elif "convolution" in expl:
            return "conv"
        elif "reduction" in expl:
            return "reduce"
        else:
            return "elem"
            
    def analyse(self, torch_code: str) -> str:
        return self.ask(f"""{torch_code}""")
