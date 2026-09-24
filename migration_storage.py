
import os
import json
from pathlib import Path
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

BUCKET = "Flyers"
JSON_PATH = Path(__file__).parent / "data" / "templates_config.json"
TEMPLATES_DIR = Path(__file__).parent / "templates"

MODE_TEST = False
TEST_ENTREPRISE = "LF"
TEST_MODELE = "IA"


supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def trouver_fichier(chemin):
    chemin = str(chemin).replace("\\", "/")

    if Path(chemin).is_absolute():
        return Path(chemin)

    if "/templates/" in chemin:
        chemin = chemin.split("/templates/", 1)[1]
    elif chemin.startswith("template/"):
        chemin = chemin[len("template/"):]
    elif chemin.startswith("templates/"):
        chemin = chemin[len("templates/"):]

    return TEMPLATES_DIR / chemin


def uploader(fichier, chemin_storage):
    fichier = Path(fichier)

    if not fichier.exists():
        print(f"❌ Fichier introuvable : {fichier}")
        return False

    with open(fichier, "rb") as f:
        supabase.storage.from_(BUCKET).upload(
            chemin_storage,
            f.read(),
            {
                "content-type": "image/png",
                "upsert": "true"
            }
        )

    print(f"✅ {fichier.name} → {chemin_storage}")
    return True


with open(JSON_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)


entreprises = [TEST_ENTREPRISE] if MODE_TEST else config.keys()
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

for entreprise in entreprises:
    modeles = config.get(entreprise, {})

    modeles_a_traiter = (
        [TEST_MODELE] if MODE_TEST and TEST_MODELE in modeles
        else modeles.keys()
    )

    for modele in modeles_a_traiter:
        data = modeles.get(modele, {})

        if not data:
            print(f"⏭️ {entreprise} / {modele} : aucun fichier")
            continue

        dossier = f"{nettoyer_nom(entreprise)}/{nettoyer_nom(modele)}"

        fichiers = {
            "base_reference": "base.png",
            "calque_fixe": "overlay.png",
            "fond_defaut": "fond.png"
        }

        for champ, nom_storage in fichiers.items():
            chemin = data.get(champ)

            if not chemin:
                continue

            if isinstance(chemin, list):
                for i, valeur in enumerate(chemin, 1):
                    fichier = trouver_fichier(valeur)
                    nom = f"overlay_{i}.png"
                    uploader(fichier, f"{dossier}/{nom}")
            else:
                fichier = trouver_fichier(chemin)
                uploader(fichier, f"{dossier}/{nom_storage}")

print("\n🎉 Migration terminée.")

