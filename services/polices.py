"""
Polices : catalogue Google Fonts + chargement des fichiers .ttf.

Trois niveaux de cache pour ne jamais retélécharger inutilement :
  1. mémoire du processus (lru_cache)
  2. Redis (partagé entre workers / redémarrages / machines)
  3. disque local (font_cache/) : PIL a besoin d'un vrai fichier
"""
import os
import re
from functools import lru_cache
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import HTTPException
from PIL import ImageFont

from config import BASE_DIR, FONTS_DIR
from services import cache

load_dotenv()

GOOGLE_FONTS_KEY = os.getenv("GOOGLE_FONTS_API_KEY")
FONT_CACHE = BASE_DIR / "font_cache"
FONT_CACHE.mkdir(exist_ok=True)

CATALOGUE_KEY = "polices:catalogue:v2"
CATALOGUE_TTL = 7 * 24 * 3600
FICHIER_TTL = 30 * 24 * 3600


@lru_cache(maxsize=1)
def google_fonts():
    """{famille: {"files": {variante: url}}} — triées par POPULARITÉ (les plus utilisées d'abord)."""
    data = cache.get_json(CATALOGUE_KEY)
    if data:
        return data

    if not GOOGLE_FONTS_KEY:
        raise HTTPException(503, "GOOGLE_FONTS_API_KEY manquante (fichier .env).")

    r = requests.get(
        "https://www.googleapis.com/webfonts/v1/webfonts",
        params={"key": GOOGLE_FONTS_KEY, "sort": "popularity"},
        timeout=10,
    )
    r.raise_for_status()
    data = {f["family"]: {"files": f.get("files", {})} for f in r.json()["items"]}
    cache.set_json(CATALOGUE_KEY, data, CATALOGUE_TTL)
    return data


@lru_cache(maxsize=1)
def resume_polices():
    """Version légère pour le frontend : {famille: {weights: [...], styles: [...]}}."""
    resultat = {}
    for nom, font in google_fonts().items():
        poids, styles = set(), set()
        for variante in font.get("files", {}):
            styles.add("italic" if variante.endswith("italic") else "normal")
            p = variante.replace("italic", "")
            try:
                poids.add(400 if p in ("", "regular") else int(p))
            except ValueError:
                continue
        resultat[nom] = {"weights": sorted(poids), "styles": sorted(styles)}
    return resultat


def font_path(name):
    p = Path(name)
    if p.is_absolute() and p.exists():
        return p
    for c in (FONTS_DIR / p.name, BASE_DIR / p, p):
        if c.exists():
            return c
    return p


def _nom_sur(texte):
    return re.sub(r"[^A-Za-z0-9_-]", "_", texte)


def _variante(weight, style):
    if style == "italic":
        return "italic" if weight == 400 else f"{weight}italic"
    return "regular" if weight == 400 else str(weight)


def _police_locale_de_secours(nom):
    """Si Google Fonts est injoignable : cherche une police du même nom dans fonts/."""
    prefixe = nom.lower().replace(" ", "")
    for f in sorted(FONTS_DIR.glob("*.[to]tf")):
        if f.stem.lower().replace(" ", "").replace("-", "").startswith(prefixe):
            return f
    return None


def _fichier_google(name, variant):
    p = FONT_CACHE / f"{_nom_sur(name)}_{variant}.ttf"
    if p.exists():
        return p

    cle = f"police:{name}:{variant}"
    data = cache.get_bytes(cle)
    if data is None:
        url = google_fonts().get(name, {}).get("files", {}).get(variant)
        if not url:
            raise HTTPException(400, f"{name} ne possède pas la variante {variant}.")
        r = requests.get(url.replace("http://", "https://"), timeout=20)
        r.raise_for_status()
        data = r.content
        cache.set_bytes(cle, data, FICHIER_TTL)

    p.write_bytes(data)
    return p


@lru_cache(maxsize=200)
def load_font(name, size, weight=400, style="normal"):
    if str(name).lower().endswith((".ttf", ".otf")):
        p = font_path(name)
    else:
        try:
            if name not in google_fonts():
                raise HTTPException(400, f"Police Google Fonts introuvable : {name}")
            p = _fichier_google(name, _variante(weight, style))
        except HTTPException as e:
            if e.status_code == 400:
                raise
            p = _police_locale_de_secours(name)
            if p is None:
                raise
        except Exception as e:
            p = _police_locale_de_secours(name)
            if p is None:
                raise HTTPException(503, f"Police {name} indisponible : {e}") from e

    try:
        return ImageFont.truetype(str(p), size)
    except Exception as e:
        raise HTTPException(400, f"Police introuvable : {name}") from e


@lru_cache(maxsize=512)
def police_a_taille(chemin, taille):
    """Même police, autre taille (utilisé pour ajuster le texte dans une zone)."""
    return ImageFont.truetype(chemin, taille)
