import io, json, os, requests
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
from fastapi import HTTPException, UploadFile
from fastapi.responses import Response
from config import OUTPUT_DIR, BASE_DIR, FONTS_DIR
from supabase import create_client
from services.IA import traduire_texte
from dotenv import load_dotenv

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
def load_image(raw):
    if not raw: return None
    path = resolve_path(raw)
    if path and path.exists():
        return Image.open(path).convert("RGBA").copy()
    try:
        data = supabase.storage.from_(SUPABASE_BUCKET).download(str(raw).replace("\\", "/"))
        return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception as e:
        raise HTTPException(404, f"Fichier introuvable : {raw}") from e


GOOGLE_FONTS_KEY = os.getenv("GOOGLE_FONTS_API_KEY")
FONT_CACHE = BASE_DIR / "font_cache"
FONT_CACHE.mkdir(exist_ok=True)


@lru_cache(maxsize=1)
def google_fonts():
    r = requests.get(
        "https://www.googleapis.com/webfonts/v1/webfonts",
        params={"key": GOOGLE_FONTS_KEY, "sort": "alpha"},
        timeout=10
    )
    r.raise_for_status()
    return {f["family"]: f for f in r.json()["items"]}


@lru_cache(maxsize=200)
def load_font(name, size, weight=400, style="normal"):
    if str(name).lower().endswith((".ttf", ".otf")):
        p = font_path(name)
    else:
        font = google_fonts().get(name)

        if not font:
            raise HTTPException(400, f"Police Google Fonts introuvable : {name}")

        variant = (
            "italic" if style == "italic" and weight == 400
            else f"{weight}italic" if style == "italic"
            else "regular" if weight == 400
            else str(weight)
        )

        url = font.get("files", {}).get(variant)

        if not url:
            raise HTTPException(
                400,
                f"{name} ne possède pas la variante {weight} {style}."
            )

        p = FONT_CACHE / f"{name}_{variant}.ttf"

        if not p.exists():
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            p.write_bytes(r.content)

    try:
        return ImageFont.truetype(str(p), size)
    except Exception as e:
        raise HTTPException(400, f"Police introuvable : {name}") from e

def font_path(name):
    p = Path(name)
    if p.is_absolute() and p.exists(): return p
    for c in (FONTS_DIR / p.name, BASE_DIR / p, p):
        if c.exists(): return c
    return p



def draw_centered_in_zone(draw, text, zone, font):
    x = int(zone.get("x", 0))
    y = int(zone.get("y", 0))
    largeur = int(zone.get("largeur", 0))
    hauteur = int(zone.get("hauteur", 0))
    taille = font.size
    taille_minimum = 20

    while taille >= taille_minimum:
        font = ImageFont.truetype(font.path, taille)
        mots, lignes, ligne = text.split(), [], ""

        for mot in mots:
            test = f"{ligne} {mot}".strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] <= largeur:
                ligne = test
            else:
                if ligne: lignes.append(ligne)
                ligne = mot

        if ligne: lignes.append(ligne)

        bbox = draw.textbbox((0, 0), "Ag", font=font)
        hauteur_ligne = bbox[3] - bbox[1]
        hauteur_totale = hauteur_ligne * len(lignes)

        if hauteur_totale <= hauteur:
            break

        taille -= 1

    if hauteur_totale > hauteur:
        lignes = lignes[:max(1, hauteur // hauteur_ligne)]

    pos_y = y + (hauteur - hauteur_ligne * len(lignes)) / 2
    alignement = zone.get("alignement", "center")

    for ligne in lignes:
        bbox = draw.textbbox((0, 0), ligne, font=font)
        largeur_ligne = bbox[2] - bbox[0]

        if alignement == "left":
            pos_x = x
        elif alignement == "right":
            pos_x = x + largeur - largeur_ligne
        else:
            pos_x = x + (largeur - largeur_ligne) / 2

        draw.text(
            (pos_x, pos_y - bbox[1]),
            ligne,
            font=font,
            fill=(255, 255, 255, 255)
        )
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

    if img.size != overlay.size:
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
        draw_centered_in_zone(draw, text, zone, font)

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