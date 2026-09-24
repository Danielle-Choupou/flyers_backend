from fastapi import HTTPException
from services.storage import save_configs

def normalize(name: str) -> str:
    return (name or "").strip().upper()

def lister_entreprises(templates):
    return {nom: list(modeles.keys()) for nom, modeles in templates.items()}

def obtenir_entreprise(nom, profiles):
    nom = normalize(nom)
    if nom not in profiles:
        raise HTTPException(404, f"L'entreprise {nom} n'existe pas.")
    return profiles[nom]

def ajouter_entreprise(data, profiles, templates):
    nom = normalize(data.get("nom"))
    if not nom:
        raise HTTPException(400, "Le nom de l'entreprise est obligatoire.")
    if nom in profiles:
        raise HTTPException(400, f"L'entreprise {nom} existe déjà.")
    profiles[nom] = {
        "nom_officiel": data.get("nom_officiel") or nom,
        "secteur_activite": data.get("secteur_activite", ""),
        "mission": data.get("mission", ""),
        "charte_editoriale": {
            "style": "professionnel",
            "niveau_formalite": "moyenne",
            "regles_strictes": {"emojis": False, "hashtags": False, "interdictions": []},
        },
    }
    templates[nom] = {}
    save_configs(profiles, templates)
    return {"status": "ok", "message": f"Entreprise {nom} créée.", "entreprise": nom}

def modifier_entreprise(nom, data, profiles, templates):
    nom = normalize(nom)
    if nom not in profiles:
        raise HTTPException(404, f"L'entreprise {nom} n'existe pas.")
    for champ in ("nom_officiel", "secteur_activite", "mission"):
        if champ in data:
            profiles[nom][champ] = data[champ]
    save_configs(profiles, templates)
    return {"status": "ok", "message": f"Entreprise {nom} modifiée.", "entreprise": nom}

def supprimer_entreprise(nom, profiles, templates):
    nom = normalize(nom)
    if nom not in profiles:
        raise HTTPException(404, f"L'entreprise {nom} n'existe pas.")
    profiles.pop(nom, None)
    templates.pop(nom, None)
    save_configs(profiles, templates)
    return {"status": "ok", "message": f"Entreprise {nom} supprimée."}
