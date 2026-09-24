# Backend — Générateur de Flyers

Backend FastAPI contenant la logique de l'application.

## Lancer

Depuis ce dossier :

    uvicorn server:app --reload

API disponible sur :

    http://127.0.0.1:8000

Test :

    http://127.0.0.1:8000/health

## Organisation

- server.py : routes API
- config.py : chemins et réglages généraux
- services/entreprises.py : gestion des entreprises
- services/modeles.py : gestion des modèles
- services/generation_flyer.py : génération des flyers
- services/images.py : traitement des images
- services/IA.py : fonctions IA
- data/ : données JSON
- templates/ : fichiers graphiques des modèles
- fonts/ : polices
- output/ : flyers générés
