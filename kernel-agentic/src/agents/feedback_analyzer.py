from .base_agent import BaseAgent


class KernelFeedbackAnalyser(BaseAgent):
    def __init__(self):
        super().__init__("kernel_feedback_analyser",
            """You are a kernel optimization and debugging expert.
                <instructions>
                - Your task is to analyze the provided kernel code and the accompanying feedback text.
                - The feedback may include error logs, performance metrics, or general observations from runtime or profiling tools.
                - Provide a detailed and clear natural language summary on how to either:
                    a) Correct the error(s) in the kernel, if the feedback is an error log, or
                    b) Improve performance and efficiency, if the feedback is performance related.
                - The explanation must be actionable and reference specific parts of the code or behavior where appropriate.
                - Mention any known optimization techniques or best practices relevant to the issue.
                - You can suggest alternative kernel primitives or changes in data layout if applicable.
                - Output a text object with:
                  - "summary": detailed guidance on how to fix or improve the kernel
                  - "issue_type": either "error", "performance", or "general"
                  - "suggested_changes": list of suggested technical modifications please include line numbers if applicable based on the error trace
                </instructions>
                Return ONLY the a string with some sections in text as "summary", "issue_type", and "suggested_changes".
            """
        )

    def classify(self, feedback_text: str) -> str:
        feedback_lower = feedback_text.lower()
        if any(keyword in feedback_lower for keyword in ["error", "exception", "traceback", "runtimeerror", "cuda error", "segfault"]):
            return "error"
        elif any(keyword in feedback_lower for keyword in ["latency", "utilization", "throughput", "slow", "inefficient", "profiling", "perf"]):
            return "performance"
        else:
            return "general"

    def analyse(self, kernel_code: str, feedback_text: str) -> str:
        issue_type = self.classify(feedback_text)
        return self.ask(f"""Kernel Code:

                            ```{kernel_code}```

                            Feedback:

                            ```{feedback_text}```

                            Classified Issue Type: {issue_type}
                        """)

