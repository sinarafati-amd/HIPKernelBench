from ddgs import DDGS
from bs4 import BeautifulSoup
import requests, yaml
from .base_agent import BaseAgent

CFG = yaml.safe_load(open("config.yml")).get("search", {})

class SearchAgent(BaseAgent):
    """
    Use DuckDuckGo to get URLs *and* scrape any code snippets
    (<pre> or <code>) from the top pages.
    """
    def __init__(self):
        super().__init__(
            role="internet_search",
            system_prompt=(
                "You are an AI that crafts concise DuckDuckGo search queries "
                "to find AMD HIP kernel examples or optimization tips."
            )
        )
        self.max_results = CFG.get("max_results", 5)
        self.ddgs        = DDGS()

    def search(self, torch_expl: str) -> str:
        # 1) Let LLM propose a focused query
        qry = self.ask("Given this PyTorch operation description, propose a 3–5 word DuckDuckGo search query "
            "to find AMD HIP kernel examples or optimization tips:\n\n"
            f"{torch_expl}\n"
        ).strip().strip('"')

        snippets = []
        # 2) Fetch top DuckDuckGo hits
        for r in self.ddgs.text(qry, max_results=self.max_results):
            title = r.get("title","").strip()
            href  = r.get("href","").strip()
            body  = r.get("body","").strip() or r.get("snippet","").strip()
            snippets.append(f"## {title}\nURL: {href}\n{body}")

            # 3) Scrape code samples from the page
            try:
                resp = requests.get(href, timeout=3)
                soup = BeautifulSoup(resp.text, "html.parser")
                # grab first two code/pre blocks
                blocks = soup.find_all(["pre","code"])
                for cb in blocks[:2]:
                    text = cb.get_text().strip()
                    if len(text.splitlines()) > 2:
                        snippets.append("```cpp\n" + text + "\n```")
            except Exception:
                # ignore pages we cannot fetch/parse
                continue

        # 4) return combined context
        return "\n\n".join(snippets)
