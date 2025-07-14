from typing import Any
from .base_agent import BaseAgent
import os 
import sys 
from pathlib import Path
import base64

dir_path=str(Path(os.path.dirname(Path(__file__))).parent)
if dir_path not in sys.path:
    sys.path.append(dir_path)


class EquationExplainer(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            role="equation_explainer",
            system_prompt=(
                "You are a concise mathematical explainer.\n"
                "Given a LaTeX equation, output short summary"
                "that summarise what the equation represents or computes. "
                "Return plain text - no code fences."
            ),
        )

    def explain(self, latex: str) -> str:
        return self.ask(f"Explain this equation in plain English:\n{latex}")


class CodeSnippetExplainer(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            role="code_snippet_explainer",
            system_prompt=(
                "You are an expert software engineer.\n"
                "Given a small code snippet, describe in short summary "
                "what the code does - focus on its high-level purpose, not line-by-line."
            ),
        )

    def explain(self, code: str) -> str:
        return self.ask(f"Explain what this code does:\n```python\n{code}\n```")


class TableExplainer(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            role="table_explainer",
            system_prompt=(
                "You are a data analyst.\n"
                "Given a CSV preview of a table, produce a short description "
                "covering:\n- what the rows represent\n- what the columns mean\n"
                "- any immediately obvious noteworthy values."
            ),
        )

    def explain(self, csv_preview: str) -> str:
        return self.ask(
            "Given the following CSV preview, describe the table:\n" + csv_preview
        )


class PictureExplainer(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            role="picture_explainer",
            system_prompt=(
                "You are a vision-language analyst.\n"
                "Given an image, produce a concise but precise description that "
                "covers what the graphic shows, the meaning of axes/legends/colors, "
                "and any obvious trends or anomalies."
            ),
        )

    def explain(self, data_uri: str) -> str:
        # send the data URI directly in the payload
        user_msg = [
            {"type": "text",  "text": "Please describe this image in detail."},
            {"type": "image_url", "image_url": {"url": data_uri}}
        ]
        return self.ask(user_msg)
