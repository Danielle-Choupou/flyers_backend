from pathlib import Path
from fastapi import HTTPException
from services.storage import (
    create_template_record,
    template_storage_path,
    upload_template_file,
)

def normalize(name: str) -> str:
    return (name or "").strip().upper()

def lister_modeles(entreprise, templates):
    entreprise = normalize(entreprise)
    if entreprise not in templates:
        raise HTTPException(404, "Entreprise introuvable.")
    return list(templates[entreprise].keys())

def obtenir_modele(entreprise, modele, templates):
    entreprise = normalize(entreprise)
    modele = (modele or "").strip()
    if entreprise not in templates:
        raise HTTPException(404, "Entreprise introuvable.")
    if modele not in templates[entreprise]:
        raise HTTPException(404, "Modèle introuvable.")
    return templates[entreprise][modele]

def lister_objectifs(entreprise, templates):
    entreprise = normalize(entreprise)
    if entreprise not in templates:
        raise HTTPException(404, "Entreprise introuvable.")
    objectifs = []
    for cfg in templates[entreprise].values():
        obj = cfg.get("objectif_publication")
        if obj and obj not in objectifs:
            objectifs.append(obj)
    return objectifs

def ajouter_modele(data, templates, profiles):
    entreprise = normalize(data.get("entreprise"))
    modele = (data.get("modele") or "").strip()
    objectif = (data.get("objectif_publication") or "").strip()
    config = data.get("config") or {}
    if not entreprise:
        raise HTTPException(400, "Entreprise obligatoire.")
    if not modele:
        raise HTTPException(400, "Nom du modèle obligatoire.")
    if not objectif:
        raise HTTPException(400, "Objectif de publication obligatoire.")
    if entreprise not in templates:
        raise HTTPException(404, "Entreprise introuvable.")
    if modele in templates[entreprise]:
        raise HTTPException(400, "Ce modèle existe déjà.")
    required = ("base_reference", "calque_fixe", "zones_modifiables")
    if any(k not in config for k in required):
        raise HTTPException(400, "Configuration du modèle incomplète.")
    config = dict(config)
    config["objectif_publication"] = objectif
    create_template_record(entreprise, modele, config)
    templates[entreprise][modele] = config
    return {"status": "ok", "message": f"Modèle {modele} enregistré.", "modele": modele, "objectif_publication": objectif, "config": config}

def save_uploaded_template_files(entreprise, modele, base_bytes, overlay_bytes):
    return {
        "base_reference": upload_template_file(entreprise, modele, "base_reference", base_bytes),
        "calque_fixe": upload_template_file(entreprise, modele, "calque_fixe", overlay_bytes),
    }

def obtenir_fichier_modele(entreprise, modele, fichier, templates):
    entreprise=normalize(entreprise)
    modele=(modele or "").strip()

    if entreprise not in templates or modele not in templates[entreprise]:
        raise HTTPException(404,"Modèle introuvable.")

    cfg=templates[entreprise][modele]
    chemin=cfg.get(fichier)

    if not chemin:
        raise HTTPException(404,"Fichier introuvable.")

    return template_storage_path(entreprise, modele, fichier)