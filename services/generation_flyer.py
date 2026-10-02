import io, json, os
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps
from fastapi import HTTPException, UploadFile
from fastapi.responses import Response
from config import OUTPUT_DIR, BASE_DIR
from supabase import create_client
from services.IA import traduire_texte
from dotenv import load_dotenv
from services.polices import load_font,police_a_taille

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_BUCKET = "Flyers"
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def resolve_path(raw):
    if not raw: return None
    p = Path(str(raw).replace("\\", os.sep))
    if p.is_absolute() and p.exists(): return p
    for c in (BASE_DIR / p, BASE_DIR / "templates" / p.name, Path(str(raw))):
        if c.exists(): return c
    return p

@lru_cache(maxsize=100)
def _load_image_cached(raw):
    if not raw: return None
    path = resolve_path(raw)
    if path and path.exists():
        return Image.open(path).convert("RGBA").copy()
    try:
        data = supabase.storage.from_(SUPABASE_BUCKET).download(str(raw).replace("\\", "/"))
        return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception as e:
        raise HTTPException(404, f"Fichier introuvable : {raw}") from e

def load_image(raw):
    return _load_image_cached(raw).copy() if raw else None

# ====================================================================== texte

def _largeur(draw, texte, font):
    bbox = draw.textbbox((0, 0), texte, font=font)
    return bbox[2] - bbox[0]


def _lignes(draw, texte, font, largeur):
    lignes, ligne = [], ""
    for mot in texte.split():
        test = f"{ligne} {mot}".strip()
        if _largeur(draw, test, font) <= largeur:
            ligne = test
        else:
            if ligne:
                lignes.append(ligne)
            ligne = mot
    if ligne:
        lignes.append(ligne)
    return lignes


def _hauteur_ligne(draw, font):
    bbox = draw.textbbox((0, 0), "Ag", font=font)
    return bbox[3] - bbox[1]


def _tient(draw, texte, font, largeur, hauteur):
    lignes = _lignes(draw, texte, font, largeur)
    if not lignes:
        return True
    if any(_largeur(draw, l, font) > largeur for l in lignes):
        return False  # un mot seul déborde en largeur
    return _hauteur_ligne(draw, font) * len(lignes) <= hauteur


def draw_centered_in_zone(draw, text, zone, font, color="#FFFFFF"):
    x = int(zone.get("x", 0))
    y = int(zone.get("y", 0))
    largeur = int(zone.get("largeur", 0))
    hauteur = int(zone.get("hauteur", 0))
    chemin = font.path
    taille_max = int(font.size)
    taille_min = min(20, taille_max)

    # Recherche dichotomique de la plus grande taille qui tient dans la zone.
    # Avant : on retirait 1 px à la fois en recréant la police à chaque tour, et
    # hauteur_totale n'existait pas si la taille de départ était < 20 (NameError).
    bas, haut, retenue = taille_min, taille_max, taille_min
    while bas <= haut:
        milieu = (bas + haut) // 2
        if _tient(draw, text, police_a_taille(chemin, milieu), largeur, hauteur):
            retenue, bas = milieu, milieu + 1
        else:
            haut = milieu - 1

    font = police_a_taille(chemin, retenue)
    lignes = _lignes(draw, text, font, largeur)
    if not lignes:
        return

    hauteur_ligne = max(1, _hauteur_ligne(draw, font))
    if hauteur_ligne * len(lignes) > hauteur:
        lignes = lignes[: max(1, hauteur // hauteur_ligne)]

    pos_y = y + (hauteur - hauteur_ligne * len(lignes)) / 2
    alignement = zone.get("alignement", "center")
    try:
        fill = ImageColor.getcolor(color, "RGBA")
    except (TypeError, ValueError):
        fill = (255, 255, 255, 255)

    for ligne in lignes:
        bbox = draw.textbbox((0, 0), ligne, font=font)
        largeur_ligne = bbox[2] - bbox[0]

        if alignement == "left":
            pos_x = x
        elif alignement == "right":
            pos_x = x + largeur - largeur_ligne
        else:
            pos_x = x + (largeur - largeur_ligne) / 2

        draw.text((pos_x, pos_y - bbox[1]), ligne, font=font, fill=fill)
        pos_y += hauteur_ligne


def generate_flexible(config, valeurs, uploaded_bytes=None, entreprise="", modele="", preview=False, fontes=None):
    overlay = load_image(config.get("calque_fixe"))

    if overlay is None:
        raise HTTPException(400, "Le modèle ne possède pas de calque fixe.")

    zones = config.get("zones_modifiables", {})
    zone_image = next(
        (z for z in zones.values() if z.get("type") == "image"),
        None
    )

    fond_path = config.get("fond_defaut")

    if fond_path:
        try:
            img = load_image(fond_path)
        except HTTPException:
            img = None
    else:
        img = None

    if img is None and uploaded_bytes:
        try:
            uploaded = Image.open(io.BytesIO(uploaded_bytes)).convert("RGBA")
        except Exception as e:
            raise HTTPException(400, "Image invalide.") from e

        img = Image.new("RGBA", overlay.size, (255, 255, 255, 255))

        if zone_image:
            box = (
                int(zone_image["largeur"]),
                int(zone_image["hauteur"])
            )
            uploaded = ImageOps.fit(uploaded, box, Image.Resampling.LANCZOS)
            img.alpha_composite(
                uploaded,
                (int(zone_image["x"]), int(zone_image["y"]))
            )
        else:
            img = ImageOps.fit(
                uploaded,
                overlay.size,
                Image.Resampling.LANCZOS
            ).convert("RGBA")

    if img is None:
        raise HTTPException(400, "Le modèle n'a pas encore d'image de base.")

    if zone_image and fond_path and not uploaded_bytes:
        zone_size = (int(zone_image["largeur"]), int(zone_image["hauteur"]))
        fond_zone = ImageOps.fit(
            img,
            zone_size,
            Image.Resampling.LANCZOS,
        ).convert("RGBA")
        img = Image.new("RGBA", overlay.size, (255, 255, 255, 255))
        img.alpha_composite(
            fond_zone,
            (int(zone_image["x"]), int(zone_image["y"])),
        )
    elif img.size != overlay.size:
        img = ImageOps.fit(
            img,
            overlay.size,
            Image.Resampling.LANCZOS
        ).convert("RGBA")

    if uploaded_bytes and fond_path and zone_image:
        try:
            uploaded = Image.open(io.BytesIO(uploaded_bytes)).convert("RGBA")
        except Exception as e:
            raise HTTPException(400, "Image invalide.") from e

        box = (
            int(zone_image["largeur"]),
            int(zone_image["hauteur"])
        )

        uploaded = ImageOps.fit(
            uploaded,
            box,
            Image.Resampling.LANCZOS
        )

        img.alpha_composite(
            uploaded,
            (int(zone_image["x"]), int(zone_image["y"]))
        )

    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    for name, zone in zones.items():
        if zone.get("type") != "texte":
            continue

        text = str(valeurs.get(name, "")).strip()
        if not text:
            continue

        fonte = (fontes or {}).get(name, {})
        font = load_font(
            fonte.get("font", zone.get("font", "Inter")),
            int(fonte.get("size", zone.get("size", 50))),
            int(fonte.get("weight", zone.get("weight", 400))),
            fonte.get("style", zone.get("style", "normal"))
        )
        draw_centered_in_zone(draw, text, zone, font, fonte.get("color", zone.get("color", "#FFFFFF")))

    if preview:
        buf = io.BytesIO()
        img.save(buf, format="PNG", compress_level=1)
        return Response(
            content=buf.getvalue(),
            media_type="image/png"
        )

    return save_and_response(img, entreprise, modele)


def generate_legacy(config, valeurs, uploaded_bytes=None, entreprise="", modele=""):
    overlay_raw = config.get("overlay")
    overlays = config.get("overlays") or ([overlay_raw] if overlay_raw else [])

    if not overlays:
        raise HTTPException(400, "Ancien modèle sans calque configuré.")

    overlay = load_image(overlays[0])
    target = overlay.size

    if uploaded_bytes:
        background = Image.open(io.BytesIO(uploaded_bytes)).convert("RGBA")
    else:
        background = load_image(config.get("fond_defaut"))

    offset = int(config.get("fond_y_offset", 0))
    canvas = Image.new("RGBA", target, (255, 255, 255, 255))

    h = max(1, target[1] - offset)
    bg = ImageOps.fit(
        background,
        (target[0], h),
        Image.Resampling.LANCZOS
    ).convert("RGBA")

    canvas.alpha_composite(bg, (0, offset))

    for raw in overlays:
        p = resolve_path(raw)
        if p:
            canvas = Image.alpha_composite(canvas, load_image(raw))

    draw = ImageDraw.Draw(canvas)

    fields = [
        ("titre_principal", "titre_y", "titre_taille"),
        ("poste", "poste_y", "poste_taille"),
        ("description", "desc_y", "desc_taille"),
        ("lieu", "lieu_y", "lieu_taille")
    ]

    for field, ykey, skey in fields:
        text = str(valeurs.get(field, "") or "").strip()

        if text and config.get(ykey) is not None:
            draw_centered(
                draw,
                canvas,
                text,
                int(config[ykey]),
                load_font(
                    "Inter_Regular.ttf",
                    int(config.get(skey, 50))
                )
            )

    return save_and_response(canvas, entreprise, modele)


def save_and_response(img, entreprise, modele):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{entreprise}_{modele}_{ts}.png".replace(" ", "_")
    path = OUTPUT_DIR / name

    img.convert("RGB").save(path, "PNG")

    buf = io.BytesIO()
    img.save(buf, format="PNG")

    return Response(
        content=buf.getvalue(),
        media_type="image/png"
    )


def generate(config, valeurs, uploaded_bytes, entreprise, modele, translate=None, preview=False, fontes=None):
    if translate:
        zones = config.get("zones_modifiables", {})

        if zones:
            for name, zone in zones.items():
                if zone.get("type") == "texte" and valeurs.get(name):
                    valeurs[name] = traduce_safe(
                        str(valeurs[name]),
                        translate
                    )
        else:
            for k in ("titre_principal", "poste", "lieu", "description"):
                if valeurs.get(k):
                    valeurs[k] = traduce_safe(valeurs[k], translate)

    if config.get("zones_modifiables"):
        return generate_flexible(
            config,
            valeurs,
            uploaded_bytes,
            entreprise,
            modele,
            preview,
            fontes
        )

    return generate_legacy(
        config,
        valeurs,
        uploaded_bytes,
        entreprise,
        modele
    )


def traduce_safe(text, lang):
    return traduire_texte(text, lang)