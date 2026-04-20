import os
from bert_score import score
import torch

def download_bert_model():
    model_name = os.getenv("BERTSCORE_MODEL", "distilbert-base-uncased")
    print(f"Pre-downloading BERTScore model: {model_name}...")
    
    # Dummy score call to trigger download
    # We use very short strings to minimize computation
    cands = ["hello"]
    refs = ["hello"]
    
    # Force download to a specific directory if needed, 
    # but by default it goes to ~/.cache/huggingface/hub
    score(cands, refs, model_type=model_name, lang="en", device="cpu")
    print("Model download complete.")

if __name__ == "__main__":
    download_bert_model()
