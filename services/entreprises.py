from copy import deepcopy

from fastapi import HTTPException
from services.storage import delete_company, save_company_profile

def normalize(name: str) -> str:
    return (name or "").strip().upper()

def lister_entreprises(templates):
    return {nom: list(modeles.keys()) for nom, modeles in templates.items()}

def obtenir_entreprise(nom, profiles):
    nom = normalize(nom)
    if nom not in profiles:
        raise HTTPException(404, f"L'entreprise {nom} n'existe pas.")
    return profiles[nom]


def _merge_profile(existing, updates):
    """Merge a submitted profile while preserving fields unknown to the editor."""
    result = deepcopy(existing) if isinstance(existing, dict) else {}
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_profile(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result

def ajouter_entreprise(data, profiles, templates):
    nom = normalize(data.get("nom"))
    if not nom:
        raise HTTPException(400, "Le nom de l'entreprise est obligatoire.")
    if nom in profiles:
        raise HTTPException(400, f"L'entreprise {nom} existe déjà.")
    submitted_profile = data.get("profile")
    if submitted_profile is not None and not isinstance(submitted_profile, dict):
        raise HTTPException(400, "Le profil de l'entreprise doit être un objet JSON.")
    profile = deepcopy(submitted_profile) if isinstance(submitted_profile, dict) else {
        "secteur_activite": data.get("secteur_activite", ""),
        "mission": data.get("mission", ""),
        "charte_editoriale": {
            "style": "professionnel",
            "niveau_formalite": "moyenne",
            "regles_strictes": {"emojis": False, "hashtags": False, "interdictions": []},
        },
    }
    profile["nom_officiel"] = (
        profile.get("nom_officiel") or data.get("nom_officiel") or nom
    )
    profile.setdefault("secteur_activite", data.get("secteur_activite", ""))
    profile.setdefault("mission", data.get("mission", ""))
    save_company_profile(nom, profile)
    profiles[nom] = profile
    templates[nom] = {}
    return {"status": "ok", "message": f"Entreprise {nom} créée.", "entreprise": nom}

def modifier_entreprise(nom, data, profiles, templates):
    nom = normalize(nom)
    if nom not in profiles:
        raise HTTPException(404, f"L'entreprise {nom} n'existe pas.")
    submitted_profile = data.get("profile")
    if submitted_profile is not None and not isinstance(submitted_profile, dict):
        raise HTTPException(400, "Le profil de l'entreprise doit être un objet JSON.")
    profile = _merge_profile(
        profiles[nom],
        submitted_profile if isinstance(submitted_profile, dict) else {
            champ: data[champ]
            for champ in ("nom_officiel", "secteur_activite", "mission")
            if champ in data
        },
    )
    save_company_profile(nom, profile)
    profiles[nom] = profile
    return {"status": "ok", "message": f"Entreprise {nom} modifiée.", "entreprise": nom}

def supprimer_entreprise(nom, profiles, templates):
    nom = normalize(nom)
    if nom not in profiles:
        raise HTTPException(404, f"L'entreprise {nom} n'existe pas.")
    delete_company(nom)
    profiles.pop(nom, None)
    templates.pop(nom, None)
    return {"status": "ok", "message": f"Entreprise {nom} supprimée."}
