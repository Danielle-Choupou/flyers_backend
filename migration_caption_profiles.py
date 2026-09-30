"""Importe les profils éditoriaux de caption.json dans Supabase.

Ce script est exécuté une fois pour migrer les données. L'application ne lit
pas caption.json pendant son fonctionnement normal.
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
caption_path = Path(__file__).parent / "data" / "caption.json"
company_code_aliases = {
    "ITAC PARTS": "ITAC",
}

with caption_path.open("r", encoding="utf-8") as file:
    caption_profiles = json.load(file)

for code, caption_profile in caption_profiles.items():
    company_code = company_code_aliases.get(code, code)
    existing = (
        supabase.table("companies")
        .select("id,nom_officiel,profile_data")
        .eq("code", company_code)
        .limit(1)
        .execute()
        .data
    )

    if existing:
        company = existing[0]
        profile_data = dict(company.get("profile_data") or {})
        profile_data.update(caption_profile)
        nom_officiel = company.get("nom_officiel") or caption_profile.get("nom_officiel", company_code)
        profile_data["nom_officiel"] = nom_officiel
        supabase.table("companies").update({
            "nom_officiel": nom_officiel,
            "profile_data": profile_data,
        }).eq("id", company["id"]).execute()
    else:
        supabase.table("companies").insert({
            "code": company_code,
            "nom_officiel": caption_profile.get("nom_officiel", code),
            "profile_data": caption_profile,
        }).execute()

    print(f"Profil caption migre : {code} -> {company_code}")

print("Migration des profils caption terminee.")