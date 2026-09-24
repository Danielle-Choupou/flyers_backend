
import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

BUCKET = "Flyers"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def nettoyer_nom(nom):
    return (
        nom.replace("é", "e")
           .replace("è", "e")
           .replace("ê", "e")
           .replace("ë", "e")
           .replace("à", "a")
           .replace("â", "a")
           .replace("ä", "a")
           .replace("î", "i")
           .replace("ï", "i")
           .replace("ô", "o")
           .replace("ö", "o")
           .replace("ù", "u")
           .replace("û", "u")
           .replace("ü", "u")
           .replace("ç", "c")
           .replace(" ", "_")
    )


companies = supabase.table("companies").select("id,code").execute().data

for company in companies:
    company_id = company["id"]
    entreprise = company["code"]

    templates = (
        supabase.table("templates")
        .select("id,name,config")
        .eq("company_id", company_id)
        .execute()
        .data
    )

    for template in templates:
        modele = template["name"]
        config = template["config"] or {}

        dossier = f"{nettoyer_nom(entreprise)}/{nettoyer_nom(modele)}"

        modifications = False

        if config.get("base_reference"):
            config["base_reference"] = f"{dossier}/base.png"
            modifications = True

        if config.get("calque_fixe"):
            if isinstance(config["calque_fixe"], list):
                config["calque_fixe"] = [
                    f"{dossier}/overlay_{i}.png"
                    for i in range(1, len(config["calque_fixe"]) + 1)
                ]
            else:
                config["calque_fixe"] = f"{dossier}/overlay.png"
            modifications = True

        if config.get("fond_defaut"):
            config["fond_defaut"] = f"{dossier}/fond.png"
            modifications = True

        if modifications:
            supabase.table("templates").update({
                "config": config
            }).eq("id", template["id"]).execute()

            print(f"✅ {entreprise} / {modele}")
        else:
            print(f"⏭️ {entreprise} / {modele} : aucun chemin à modifier")

print("\n🎉 Mise à jour des chemins terminée.")

