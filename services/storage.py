import os
import unicodedata
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
BUCKET = "Flyers"


def _storage_name(value):
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return value.replace(" ", "_")


def template_storage_path(entreprise, modele, fichier):
    noms = {
        "base_reference": "base.png",
        "calque_fixe": "overlay.png",
        "fond_defaut": "fond.png",
    }
    return f"{_storage_name(entreprise)}/{_storage_name(modele)}/{noms[fichier]}"


def upload_template_file(entreprise, modele, fichier, content):
    chemin = template_storage_path(entreprise, modele, fichier)
    supabase.storage.from_(BUCKET).upload(
        chemin, content, {"content-type": "image/png", "upsert": "true"}
    )
    return chemin


def download_template_file(chemin):
    return supabase.storage.from_(BUCKET).download(chemin)


def save_company_profile(code, profile):
    data = profile or {}
    supabase.table("companies").upsert(
        {
            "code": code,
            "nom_officiel": data.get("nom_officiel", code),
            "profile_data": data,
        },
        on_conflict="code",
    ).execute()


def delete_company(code):
    company = (
        supabase.table("companies")
        .select("id")
        .eq("code", code)
        .limit(1)
        .execute()
        .data
    )
    if not company:
        raise ValueError(f"Entreprise introuvable : {code}")

    company_id = company[0]["id"]
    templates = (
        supabase.table("templates")
        .select("name")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )
    files = [
        template_storage_path(code, template["name"], fichier)
        for template in templates
        for fichier in ("base_reference", "calque_fixe", "fond_defaut")
    ]
    if files:
        supabase.storage.from_(BUCKET).remove(files)

    supabase.table("templates").delete().eq("company_id", company_id).execute()
    supabase.table("companies").delete().eq("id", company_id).execute()


def create_template_record(entreprise, modele, config):
    company = (
        supabase.table("companies")
        .select("id")
        .eq("code", entreprise)
        .limit(1)
        .execute()
        .data
    )
    if not company:
        raise ValueError(f"Entreprise introuvable : {entreprise}")

    config_data = dict(config)
    objective = config_data.pop("objectif_publication", None)
    supabase.table("templates").insert(
        {
            "company_id": company[0]["id"],
            "name": modele,
            "objective": objective,
            "config": config_data,
        }
    ).execute()


def save_template_config(entreprise, modele, config):
    company = (
        supabase.table("companies")
        .select("id")
        .eq("code", entreprise)
        .limit(1)
        .execute()
        .data
    )
    if not company:
        raise ValueError(f"Entreprise introuvable : {entreprise}")

    template = (
        supabase.table("templates")
        .select("id")
        .eq("company_id", company[0]["id"])
        .eq("name", modele)
        .limit(1)
        .execute()
        .data
    )
    if not template:
        raise ValueError(f"Modèle introuvable : {modele}")

    config_data = dict(config)
    objective = config_data.pop("objectif_publication", None)
    supabase.table("templates").update({
        "objective": objective,
        "config": config_data,
    }).eq("id", template[0]["id"]).execute()


def delete_template(entreprise, modele):
    company = (
        supabase.table("companies")
        .select("id")
        .eq("code", entreprise)
        .limit(1)
        .execute()
        .data
    )
    if not company:
        raise ValueError(f"Entreprise introuvable : {entreprise}")

    template = (
        supabase.table("templates")
        .select("id")
        .eq("company_id", company[0]["id"])
        .eq("name", modele)
        .limit(1)
        .execute()
        .data
    )
    if not template:
        raise ValueError(f"Modèle introuvable : {modele}")

    fichiers = [
        template_storage_path(entreprise, modele, fichier)
        for fichier in ("base_reference", "calque_fixe", "fond_defaut")
    ]
    supabase.storage.from_(BUCKET).remove(fichiers)
    supabase.table("templates").delete().eq("id", template[0]["id"]).execute()


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

        config = dict(template.get("config") or {})
        for fichier in ("base_reference", "calque_fixe", "fond_defaut"):
            if config.get(fichier):
                config[fichier] = template_storage_path(code, template["name"], fichier)

        if template.get("objective"):
            config["objectif_publication"] = template["objective"]

        configs.setdefault(code, {})[template["name"]] = config

    return profiles, configs



