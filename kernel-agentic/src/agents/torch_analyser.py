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

    def analyse(self, torch_code: str) -> str:
        return self.ask(f"""<thinking>
                {torch_code}
                </thinking>""")
