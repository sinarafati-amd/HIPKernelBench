from datetime import datetime
import json, yaml, os, uuid, logging

class JSONLogger:
    def __init__(self, history_file: str):
        self.path = history_file
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s | %(levelname)s | %(message)s")

    def append(self, record: dict):
        record["ts"] = datetime.utcnow().isoformat()

        def _fallback(o):
            if hasattr(o, "model_dump"):
                 return o.model_dump()
            return str(o)   

        with open(self.path, "a") as fp:
            fp.write(json.dumps(record, default=_fallback)+"\n")

log = JSONLogger(history_file=yaml.safe_load(open("config.yml"))["logging"]["history_file"])
