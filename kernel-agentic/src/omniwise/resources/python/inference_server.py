# SPDX-License-Identifier: MIT
# Copyright (c) 2024 Advanced Micro Devices, Inc. All rights reserved.

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import argparse
import torch
import re
from concurrent.futures import ThreadPoolExecutor

from transformers import AutoTokenizer, AutoModel
import torch
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
)
import re
import time
import json


import asyncio
import aiofiles
import hashlib
import os
from .omnimodel import OmniModel
import base64


async def write_to_file_async(post_body, post_data):
    if not isinstance(post_data, bytes):
        raise ValueError("post_data must be a bytes object")
    hash_object = hashlib.sha256(post_data)
    hash_hex = hash_object.hexdigest()
    cache_path = "~/.cache/omniwise/logs"
    cache_path = os.path.expanduser("~/.cache/logs")
    filename = f"{cache_path}/{hash_hex}.json"
    os.makedirs(cache_path, exist_ok=True)
    post_body_json = json.dumps(post_body, indent=4)
    async with aiofiles.open(filename, "w") as file:
        await file.write(post_body_json)
    print(f"Data written to {filename}")


def MakeHandlerClassFromArgs(init_args):
    class CustomHandler(BaseHTTPRequestHandler):
        def predict(self, code, architecture, compiler_flags, top_k, top_p, temperature):
            omnimodel = init_args.get("model")
            question = f"For the GPU architecture {architecture} and the compiler flags {compiler_flags}, what are the bandwidth, arithmetic intensity, hit rates, flops of the following code? Output the answer in JSON format."
            print(f"Predicting: {code} {question}")
            result_json = omnimodel.predict(
                code=code, question=question, top_k = top_k, top_p = top_p,
                temperature = temperature, json_only=True
            )
            return result_json

        def do_POST(self):

            try:
                start_inference_time = time.time()
                content_length = int(self.headers.get("Content-Length", 0))

                post_data = self.rfile.read(content_length)
                print(f"Received: {post_data}")

                try:
                    post_body = json.loads(post_data)
                    compiler_flags = post_body["compiler_flags"]
                    architecture = post_body["architecture"]
                    encoded_code = post_body["code"]
                    code = base64.b64decode(encoded_code).decode("utf-8")
                    print(f"compiler_flags: {compiler_flags}")
                    print(f"architecture: {architecture}")
                    print(f"encoded_code: {encoded_code}")
                    print(f"code: {code}")

                except json.JSONDecodeError as e:
                    print(f"Error decoding JSON: {e}")
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Invalid JSON payload")
                    return

                if init_args.get("cache_kernels"):
                    print("Caching kernels.....")
                    asyncio.run(write_to_file_async(post_body, post_data))
                top_k = init_args.get("top_k")
                top_p = init_args.get("top_p")
                temperature = init_args.get("temperature")
                result = self.predict(
                    code=code, architecture=architecture, compiler_flags=compiler_flags,
                    top_k = top_k, top_p = top_p,
                    temperature = temperature
                )

                response_json = f"""
                {{
                    "message": "Received",
                    "data": {{
                        "compiler_flags": "{result['compiler_flags']}",
                        "architecture": "{result['architecture']}",
                        "L1 Bandwidth (GB/s)": {float(result['L1_Cache_Bandwidth']) * 1000.},
                        "L2 Bandwidth (GB/s)": {float(result['L2_Cache_Bandwidth']) * 1000.},
                        "HBM Read Bandwidth (GB/s)": {float(result['L2_Fabric_Write_BW']) * 1000.},
                        "HBM Write Bandwidth (GB/s)": {float(result['L2_Fabric_Read_BW']) * 1000.},
                        "L1 Hit Rate (%)": {float(result['L1_Cache_Hit_Rate']) * 100.},
                        "L2 Hit Rate (%)": {float(result['L2_Cache_Hit_Rate']) * 100.},
                        "L1 GFLOP/S": {float(result['L1_Cache_GFLOPS']) * 1000.},
                        "L2 GFLOP/S": {float(result['L2_Cache_GFLOPS']) * 1000.},
                        "HBM GFLOP/S": {float(result['HBM_GFLOPS']) * 1000.},
                        "L1 AI (FLOPs/Byte)": {float(result['L1_Cache_Arithmetic_Intensity'])},
                        "L2 AI (FLOPs/Byte)": {float(result['L2_Cache_Arithmetic_Intensity'])},
                        "HBM AI (FLOPs/Byte)": {float(result['HBM_Arithmetic_Intensity'])}
                    }}
                }}
                """

                end_inference_time = time.time()

                print(f"{result}, {end_inference_time - start_inference_time} seconds")
                print(f"Response: {response_json}")

                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(response_json.encode("utf-8"))

            except Exception as e:
                print(f"Caught {str(e)} : {result}")
                self.send_error(500, message=str(e))

    return CustomHandler


class OmniwiseInferenceServer(HTTPServer):
    def __init__(self, args):
        server_address = (args["host"], args["port"])

        tokenizer_path = "omniwise/omniwise-0.1.0-3b"
        model_path = "omniwise/omniwise-0.1.0-3b"

        args["model"] = OmniModel(
            tokenizer_path=tokenizer_path,
            model_path=model_path,
            device_id=args["device"],
        )
        print("Loading completed.")

        handler_class = MakeHandlerClassFromArgs(args)
        super().__init__(server_address, handler_class)
        self.is_serving = False

    def serve(self):
        self.is_serving = True
        print(f"Server started on {self.server_address[0]}:{self.server_address[1]}")
        while self.is_serving:
            self.handle_request()

    def stop(self):
        self.is_serving = False
        self.server_close()
        print("Server stopped.")


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Omniwise Inference Server")

    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Hostname or IP address of the server (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8081,
        help="Port number to connect to (default: 8081)",
    )
    parser.add_argument(
        "--device", type=int, default=0, help="GPU ID to use (default: 0)"
    )

    parser.add_argument(
        "--cache_kernels",
        default=True,
        action="store_true",
        help="Enable kernel caching (default: true)",
    )
    parser.add_argument("--top_k", type=int, default=5, help="Model output top_k")
    parser.add_argument("--top_p", type=float, default=0.9, help="Model output top_p")
    parser.add_argument(
        "--temperature", type=float, default=0.2, help="Model output temperature"
    )
    args = parser.parse_args()
    args = vars(args)

    server = OmniwiseInferenceServer(args)
    print("Now serving...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.stop()
