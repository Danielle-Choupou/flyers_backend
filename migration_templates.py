import json
import os
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

DATA_DIR = Path(__file__).parent / "data"
CONFIG_FILE = DATA_DIR / "templates_config.json"

with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    templates_config = json.load(f)

companies = supabase.table("companies").select("id, code").execute().data
company_ids = {c["code"]: c["id"] for c in companies}

for company_code, models in templates_config.items():
    company_id = company_ids.get(company_code)

    if not company_id:
        print(f"⚠️ Entreprise introuvable : {company_code}")
        continue

    for model_name, config in models.items():
        data = {
            "company_id": company_id,
            "name": model_name,
            "objective": config.get("objectif_publication"),
            "config": config
        }

        supabase.table("templates").upsert(
            data,
            on_conflict="company_id,name"
        ).execute()

        print(f"✅ {company_code} → {model_name}")

print("\nMigration des modèles terminée.")