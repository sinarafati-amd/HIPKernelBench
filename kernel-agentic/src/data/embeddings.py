from langchain_huggingface import HuggingFaceEmbeddings
def get_embedder():
    return HuggingFaceEmbeddings(model_name="intfloat/e5-base-v2")
