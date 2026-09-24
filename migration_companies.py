import json, os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

with open("data/company_profiles.json", "r", encoding="utf-8") as f:
    companies = json.load(f)

for code, profile in companies.items():
    data = {
        "code": code,
        "nom_officiel": profile.get("nom_officiel", code),
        "profile_data": {
            k: v for k, v in profile.items()
            if k != "nom_officiel"
        }
    }

    supabase.table("companies").upsert(
        data,
        on_conflict="code"
    ).execute()

    print(f"✓ {code}")

print("Migration terminée.")