import io
import os
from fastapi import HTTPException
from fastapi.responses import Response
from huggingface_hub import InferenceClient
from dotenv import load_dotenv

load_dotenv()

HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY")
HF_IMAGE_MODEL = "black-forest-labs/FLUX.1-schnell"



def generate_ai_image(prompt: str):
    if not HUGGINGFACE_API_KEY:
        raise HTTPException(500, "Clé HUGGINGFACE_API_KEY manquante sur le serveur (fichier .env).")
    if not (prompt or "").strip():
        raise HTTPException(400, "Le prompt ne peut pas être vide.")
    try:
        client = InferenceClient(token=HUGGINGFACE_API_KEY)
        image = client.text_to_image(prompt=prompt.strip(), model=HF_IMAGE_MODEL)
        buf = io.BytesIO(); image.save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")
    except Exception as e:
        raise HTTPException(500, f"Erreur Hugging Face : {e}")

