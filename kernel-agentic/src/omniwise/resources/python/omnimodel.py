# SPDX-License-Identifier: MIT
# Copyright (c) 2024 Advanced Micro Devices, Inc. All rights reserved.

from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
)
import torch
import re
import json


class OmniModel:
    def __init__(
        self, tokenizer_path: str, model_path: str = "", device_id: int = None
    ):
        self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)
        if model_path:
            self.model = AutoModelForCausalLM.from_pretrained(
                model_path, torch_dtype=torch.float16
            )
            if device_id != None:
                self.device = torch.device(
                    f"cuda:{device_id}" if torch.cuda.is_available() else "cpu"
                )
            else:
                self.device = "cpu"

            self.model.to(self.device)
        print(f"The device is on: {self.device}")
        self.tokenizer.pad_token = "<|finetune_right_pad_id|>"

    def tokenize(self, code, question, answer):
        system_prompt = "You are an expert GPU programmer and profiler. Given a code, you will predict its performance counters."
        code_block = f"```hip\n{code}\n```"
        prompt = (
            "<|start_header_id|>system<|end_header_id|>" + system_prompt + "<|eot_id|>"
            "<|start_header_id|>user<|end_header_id|>"
            + question
            + code_block
            + "<|eot_id|>"
            + "<|start_header_id|>assistant<|end_header_id|>"
            + answer
            + (self.tokenizer.special_tokens_map["eos_token"] if answer else "")
        )
        tokenized_tuple = self.tokenizer(prompt, return_tensors="pt", padding=True)
        return (
            tokenized_tuple["input_ids"][0],
            tokenized_tuple["attention_mask"][0],
            prompt,
        )

    def __extract_and_parse_json(self, text):
        json_pattern = r"```json\n(.*?)\n```"
        match = re.search(json_pattern, text, re.DOTALL)
        if match:
            json_string = match.group(1)
            try:
                return json.loads(json_string)
            except json.JSONDecodeError as e:
                print("Invalid JSON:", e)
        return None

    def predict(
        self,
        code,
        question,
        top_k=10,
        top_p=0.9,
        temperature=1,
        max_length=3072,
        json_only=False,
    ):
        input_ids, attention_mask, _ = self.tokenize(
            code=code, question=question, answer=""
        )

        pad_token_id = self.tokenizer.get_vocab()[self.tokenizer.pad_token]
        try:
            with torch.no_grad():
                predicted_tokens = (
                    self.model.generate(
                        input_ids=input_ids.unsqueeze(0).to(self.device),
                        attention_mask=attention_mask.unsqueeze(0).to(self.device),
                        max_length=max_length,
                        do_sample=True,
                        top_k=top_k,
                        top_p=top_p,
                        temperature=temperature,
                        pad_token_id=pad_token_id,
                    )
                    .cpu()
                    .numpy()
                )
            result = self.tokenizer.batch_decode(
                predicted_tokens, skip_special_tokens=False
            )

            if json_only:
                result = self.__extract_and_parse_json(result[0])

        except Exception as e:
            print(f"An error occurred: {e}")
            return ""

        return result

    def push_to_hub(
        self,
        repo_id: str,
        commit_message: str = None,
        token: str = None,
        create_pr: bool = False,
    ):
        self.model.push_to_hub(
            repo_id=repo_id,
            commit_message=commit_message,
            token=token,
            create_pr=create_pr,
        )
        self.tokenizer.push_to_hub(
            repo_id=repo_id,
            commit_message=commit_message,
            token=token,
            create_pr=create_pr,
        )
