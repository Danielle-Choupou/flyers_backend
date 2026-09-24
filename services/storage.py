import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def load_configs():
    companies = supabase.table("companies").select(
        "id,code,nom_officiel,profile_data"
    ).execute().data

    templates = supabase.table("templates").select(
        "id,company_id,name,objective,config"
    ).execute().data

    profiles = {}
    configs = {}

    for company in companies:
        code = company["code"]
        profile = company.get("profile_data") or {}

        if not profile:
            profile = {"nom_officiel": company.get("nom_officiel", code)}
        elif "nom_officiel" not in profile:
            profile["nom_officiel"] = company.get("nom_officiel", code)

        profiles[code] = profile
        configs[code] = {}

    company_codes = {
        company["id"]: company["code"]
        for company in companies
    }

    for template in templates:
        code = company_codes.get(template["company_id"])

        if not code:
            continue

        config = template.get("config") or {}

        if template.get("objective"):
            config["objectif_publication"] = template["objective"]

        configs.setdefault(code, {})[template["name"]] = config

    return profiles, configs


def save_configs(profiles, templates):
    companies = supabase.table("companies").select(
        "id,code"
    ).execute().data

    company_ids = {
        company["code"]: company["id"]
        for company in companies
    }

    for code, profile in profiles.items():
        data = profile or {}

        nom_officiel = data.get("nom_officiel", code)

        if code in company_ids:
            supabase.table("companies").update({
                "nom_officiel": nom_officiel,
                "profile_data": data
            }).eq("id", company_ids[code]).execute()
        else:
            result = supabase.table("companies").insert({
                "code": code,
                "nom_officiel": nom_officiel,
                "profile_data": data
            }).execute()

            if result.data:
                company_ids[code] = result.data[0]["id"]

    for code, modeles in templates.items():
        if code not in company_ids:
            continue

        company_id = company_ids[code]

        for nom, config in modeles.items():
            config = dict(config or {})
            objective = config.pop("objectif_publication", None)

            existing = (
                supabase.table("templates")
                .select("id")
                .eq("company_id", company_id)
                .eq("name", nom)
                .execute()
                .data
            )

            data = {
                "company_id": company_id,
                "name": nom,
                "objective": objective,
                "config": config
            }

            if existing:
                supabase.table("templates").update(
                    data
                ).eq("id", existing[0]["id"]).execute()
            else:
                supabase.table("templates").insert(data).execute()
