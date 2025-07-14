from .base_agent import BaseAgent


class TorchAnalyser(BaseAgent):
    def __init__(self):
        super().__init__("torch_analyser",
            """You are an expert PyTorch reviewer.
                <rules>
                - Produce a concise but complete natural-language explanation of what the given code computes.
                - Include tensor shapes, computational steps, and implicit broadcasting.
                </rules>
                Return ONLY the explanation in form of a json with key "explanation" 
                also consider another key "top_kernels" with which you should return a list of kernels that you think are the most relevant to the explanation. 
                here are the kernels you can use:

                ["elementwise_add","elementwise_mul","elementwise_sub","elementwise_div","elementwise_exp","sigmoid","gelu","tanh","fused_bias_add_relu","reduction_sum","reduction_max","reduction_mean","argmax","prefix_sum_scan","matrix_multiplication_gemm","conv2d_forward","max_pool2d_forward","avg_pool2d_forward","softmax","batchnorm_forward","layernorm_forward","dropout_forward","transpose","gather","scatter_add","dot_product","matrix_vector_multiplication","matrix_scalar_multiplication","batched_matrix_multiplication","3D_tensor_matrix_multiplication","matmul_transposed_A","conv_transposed_2d","conv2d_asymmetric_kernel","conv_transposed_1d"]
                output should be a json with keys "explanation" and "top_kernels" like this:
                {
                    "explanation": "Your explanation here",
                    "top_kernels": ["kernel1", "kernel2", ...]
                }

                please note that if there is no relevant kernel, you can return an empty list for "top_kernels".
                """
                )

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
