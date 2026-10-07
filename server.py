import asyncio
import json
import logging
import time
import uuid
import threading
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from services.entreprises import lister_entreprises, obtenir_entreprise, ajouter_entreprise, modifier_entreprise, supprimer_entreprise
from services.modeles import ajouter_modele, obtenir_modele, lister_modeles, lister_objectifs, save_uploaded_template_files, obtenir_fichier_modele
from services.images import  generate_ai_image
from services.IA import CaptionAdaptationRequest, CaptionRequest, adapt_caption, generate_caption
from services.generation_flyer import generate
from services import cache
from fastapi.responses import Response
from services.storage import create_template_record, delete_template, download_template_file, load_configs, save_template_config, supabase, upload_template_file
from functools import lru_cache
import hashlib
from services.generation_flyer import generate
from services.polices import resume_polices

@asynccontextmanager
async def app_lifespan(_app):
    await asyncio.to_thread(initialize_config)
    yield


app=FastAPI(title="Générateur de Flyers - Backend", lifespan=app_lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
performance_log = logging.getLogger("api.performance")
config_log = logging.getLogger("api.config")
CONFIG_CACHE_KEY = "config:snapshot:v1"
CONFIG_CACHE_TTL = 30 * 24 * 3600
CONFIG_RETRY_INTERVAL = 15
COMPANY_PROFILES = {}
TEMPLATES_CONFIG = {}
CONFIG_SOURCE = "unavailable"
LAST_CONFIG_ERROR = None
_last_config_attempt = 0.0
_config_lock = threading.Lock()


def _apply_config_snapshot(profiles, templates, source):
    global COMPANY_PROFILES, TEMPLATES_CONFIG, CONFIG_SOURCE
    COMPANY_PROFILES = profiles
    TEMPLATES_CONFIG = templates
    CONFIG_SOURCE = source


def _save_config_snapshot(profiles=None, templates=None):
    profiles = COMPANY_PROFILES if profiles is None else profiles
    templates = TEMPLATES_CONFIG if templates is None else templates
    if isinstance(profiles, dict) and isinstance(templates, dict):
        cache.set_json(
            CONFIG_CACHE_KEY,
            {"profiles": profiles, "templates": templates},
            ttl=CONFIG_CACHE_TTL,
        )


def refresh_config(force=False):
    """Refresh from Supabase, falling back to the last shared Redis snapshot."""
    global LAST_CONFIG_ERROR, _last_config_attempt
    if CONFIG_SOURCE == "supabase" and not force:
        return CONFIG_SOURCE
    now = time.monotonic()
    if not force and now - _last_config_attempt < CONFIG_RETRY_INTERVAL:
        return CONFIG_SOURCE

    with _config_lock:
        now = time.monotonic()
        if not force and now - _last_config_attempt < CONFIG_RETRY_INTERVAL:
            return CONFIG_SOURCE
        _last_config_attempt = now

        for attempt, delay in enumerate((0, 0.5, 1.5), start=1):
            if delay:
                time.sleep(delay)
            try:
                profiles, templates = load_configs()
                _apply_config_snapshot(profiles, templates, "supabase")
                LAST_CONFIG_ERROR = None
                _save_config_snapshot(profiles, templates)
                config_log.info("Configuration chargée depuis Supabase")
                return CONFIG_SOURCE
            except Exception as exc:
                LAST_CONFIG_ERROR = type(exc).__name__
                config_log.warning(
                    "Chargement Supabase échoué (essai %s/3, erreur=%s)",
                    attempt,
                    type(exc).__name__,
                )

        snapshot = cache.get_json(CONFIG_CACHE_KEY)
        if (
            isinstance(snapshot, dict)
            and isinstance(snapshot.get("profiles"), dict)
            and isinstance(snapshot.get("templates"), dict)
        ):
            _apply_config_snapshot(
                snapshot["profiles"],
                snapshot["templates"],
                "redis_stale",
            )
            config_log.warning("Mode dégradé : configuration de secours Redis en lecture seule")
        elif COMPANY_PROFILES and TEMPLATES_CONFIG:
            _apply_config_snapshot(COMPANY_PROFILES, TEMPLATES_CONFIG, "memory_stale")
            config_log.warning("Mode dégradé : dernière configuration mémoire en lecture seule")
        else:
            _apply_config_snapshot({}, {}, "unavailable")
            config_log.error("Aucune configuration de secours disponible")

    return CONFIG_SOURCE


def _route_uses_config(path):
    return path.startswith((
        "/entreprises",
        "/modeles",
        "/objectifs",
        "/historique",
        "/preview",
        "/generate",
        "/adapt-caption",
        "/generate-caption",
    ))


def _route_writes_supabase(method, path):
    if path == "/historique":
        return method == "POST"
    if path == "/entreprises" or path.startswith("/entreprises/"):
        return method in {"POST", "PUT", "DELETE"}
    if path == "/modeles" or path.startswith("/modeles/"):
        return method in {"POST", "PUT", "DELETE"}
    return False


@app.middleware("http")
async def measure_request_duration(request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    started_at = time.perf_counter()

    path = request.url.path
    if path not in {"/health", "/ready", "/reload-config"}:
        source = await asyncio.to_thread(refresh_config)
        if _route_uses_config(path) and source == "unavailable":
            duration_ms = (time.perf_counter() - started_at) * 1000
            response = JSONResponse(
                status_code=503,
                content={
                    "detail": "Configuration indisponible : Supabase ne répond pas et aucun snapshot de secours n'est disponible.",
                    "retry_after_seconds": CONFIG_RETRY_INTERVAL,
                },
                headers={
                    "Retry-After": str(CONFIG_RETRY_INTERVAL),
                    "X-Request-ID": request_id,
                    "X-Process-Time-Ms": f"{duration_ms:.2f}",
                },
            )
            performance_log.warning(
                "api_request request_id=%s method=%s path=%s status_code=503 duration_ms=%.2f config_source=unavailable",
                request_id,
                request.method,
                path,
                duration_ms,
            )
            return response
        if source != "supabase" and _route_writes_supabase(request.method, path):
            duration_ms = (time.perf_counter() - started_at) * 1000
            response = JSONResponse(
                status_code=503,
                content={
                    "detail": "Mode dégradé en lecture seule : Supabase n'est pas disponible; réessaie plus tard.",
                    "retry_after_seconds": CONFIG_RETRY_INTERVAL,
                },
                headers={
                    "Retry-After": str(CONFIG_RETRY_INTERVAL),
                    "X-Request-ID": request_id,
                    "X-Process-Time-Ms": f"{duration_ms:.2f}",
                },
            )
            performance_log.warning(
                "api_request request_id=%s method=%s path=%s status_code=503 duration_ms=%.2f config_source=%s",
                request_id,
                request.method,
                path,
                duration_ms,
                source,
            )
            return response

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - started_at) * 1000
        performance_log.exception(
            "api_request request_id=%s method=%s path=%s status_code=500 duration_ms=%.2f",
            request_id,
            request.method,
            request.url.path,
            duration_ms,
        )
        raise

    duration_ms = (time.perf_counter() - started_at) * 1000
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time-Ms"] = f"{duration_ms:.2f}"
    performance_log.info(
        "api_request request_id=%s method=%s path=%s status_code=%s duration_ms=%.2f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response

def initialize_config():
    refresh_config(force=True)

def reload():
    source = refresh_config(force=True)
    if source != "supabase":
        raise HTTPException(503, "Supabase reste indisponible; configuration de secours conservée en lecture seule.")
    return source


@app.get('/health')
def health():
    return {
        'status': 'ok' if CONFIG_SOURCE == 'supabase' else 'degraded',
        'message': 'Backend opérationnel' if CONFIG_SOURCE == 'supabase' else 'Backend en mode dégradé',
        'config_source': CONFIG_SOURCE,
        'cache': 'redis' if cache.actif() else 'désactivé',
    }


@app.get('/ready')
def ready():
    if CONFIG_SOURCE != 'supabase':
        raise HTTPException(
            503,
            detail={
                'status': 'not_ready',
                'config_source': CONFIG_SOURCE,
                'retry_after_seconds': CONFIG_RETRY_INTERVAL,
            },
            headers={'Retry-After': str(CONFIG_RETRY_INTERVAL)},
        )
    return {'status': 'ready', 'config_source': CONFIG_SOURCE}


@app.get('/fonts')
def get_fonts():
    return resume_polices()

@app.post('/reload-config')
def reload_config():
    source = reload()
    return {'status':'ok','config_source':source,'entreprises_templates':list(TEMPLATES_CONFIG),'entreprises_profils':list(COMPANY_PROFILES)}
@app.get('/entreprises')
def get_entreprises(): return lister_entreprises(TEMPLATES_CONFIG)
@app.get('/entreprises/{nom}')
def get_entreprise(nom:str): return obtenir_entreprise(nom,COMPANY_PROFILES)
@app.post('/entreprises')
def post_entreprise(data:dict):
    result = ajouter_entreprise(data,COMPANY_PROFILES,TEMPLATES_CONFIG)
    _save_config_snapshot()
    return result
@app.put('/entreprises/{nom}')
def put_entreprise(nom:str,data:dict):
    result = modifier_entreprise(nom,data,COMPANY_PROFILES,TEMPLATES_CONFIG)
    _save_config_snapshot()
    return result
@app.delete('/entreprises/{nom}')
def delete_entreprise(nom:str):
    result = supprimer_entreprise(nom,COMPANY_PROFILES,TEMPLATES_CONFIG)
    _save_config_snapshot()
    return result
@app.post('/modeles')
def post_modele(data:dict):
    result = ajouter_modele(data,TEMPLATES_CONFIG,COMPANY_PROFILES)
    _save_config_snapshot()
    return result
@app.get('/modeles/{entreprise}')
def get_modeles(entreprise:str): return lister_modeles(entreprise,TEMPLATES_CONFIG)
@app.delete('/modeles/{entreprise}/{modele}')
def delete_modele(entreprise:str,modele:str):
    ent=entreprise.strip().upper(); nom=modele.strip()
    if ent not in TEMPLATES_CONFIG or nom not in TEMPLATES_CONFIG[ent]:
        raise HTTPException(404,'Modèle introuvable.')
    try:
        delete_template(ent, nom)
    except Exception as e:
        raise HTTPException(500, f'Erreur pendant la suppression du modèle : {e}') from e
    TEMPLATES_CONFIG[ent].pop(nom, None)
    _save_config_snapshot()
    return {'status':'ok','message':f'Modèle {nom} supprimé.','entreprise':ent,'modele':nom}
@app.get('/modeles/{entreprise}/catalogue')
def get_catalogue_modeles(entreprise: str):
    ent = entreprise.strip().upper()
    if ent not in TEMPLATES_CONFIG:
        raise HTTPException(404, 'Entreprise introuvable.')

    modeles = TEMPLATES_CONFIG[ent]
    objectifs = list(dict.fromkeys(
        cfg.get('objectif_publication')
        for cfg in modeles.values()
        if cfg.get('objectif_publication')
    ))
    return {'modeles': modeles, 'objectifs': objectifs}

@app.post('/modeles/complet')
async def post_modele_complet(entreprise:str=Form(...),modele:str=Form(...),objectif_publication:str=Form(...),zones_modifiables:str=Form('{}'),base_reference:UploadFile=File(...),calque_fixe:UploadFile=File(...)):
    ent=entreprise.strip().upper(); nom=modele.strip(); obj=objectif_publication.strip()
    if ent not in TEMPLATES_CONFIG: raise HTTPException(404,'Entreprise introuvable.')
    if not nom: raise HTTPException(400,'Nom du modèle obligatoire.')
    if not obj: raise HTTPException(400,'Objectif de publication obligatoire.')
    if nom in TEMPLATES_CONFIG[ent]: raise HTTPException(400,'Ce modèle existe déjà.')
    try:
        zones = json.loads(zones_modifiables)
    except Exception as e:
        raise HTTPException(400, 'Format des zones incorrect.') from e
    if not isinstance(zones, dict):
        raise HTTPException(400, 'Les zones doivent être un objet JSON.')

    paths=save_uploaded_template_files(ent,nom,await base_reference.read(),await calque_fixe.read())
    cfg={**paths,'zones_modifiables':zones,'objectif_publication':obj}
    try:
        create_template_record(ent, nom, cfg)
    except Exception as e:
        supabase.storage.from_('Flyers').remove(list(paths.values()))
        raise HTTPException(500, f'Erreur pendant la création du modèle : {e}') from e

    TEMPLATES_CONFIG[ent][nom]=cfg
    _save_config_snapshot()
    return {'status':'ok','entreprise':ent,'modele':nom,'objectif_publication':obj,'config':cfg}

@app.put('/modeles/{entreprise}/{modele}')
def put_modele(entreprise:str,modele:str,data:dict):
    ent=entreprise.strip().upper(); nom=modele.strip()
    if ent not in TEMPLATES_CONFIG or nom not in TEMPLATES_CONFIG[ent]: raise HTTPException(404,'Modèle introuvable.')
    cfg=TEMPLATES_CONFIG[ent][nom]; cfg['zones_modifiables']=data.get('zones_modifiables',cfg.get('zones_modifiables',{}))
    if data.get('objectif_publication'): cfg['objectif_publication']=data['objectif_publication']
    try:
        save_template_config(ent, nom, cfg)
    except Exception as e:
        raise HTTPException(500, f'Erreur pendant la sauvegarde du modèle : {e}') from e
    _save_config_snapshot()
    return {'status':'ok','config':cfg}
@app.put('/modeles/{entreprise}/{modele}/fichiers')
async def put_modele_fichiers(entreprise:str,modele:str,objectif_publication:Optional[str]=Form(None),zones_modifiables:str=Form('{}'),base_reference:Optional[UploadFile]=File(None),calque_fixe:Optional[UploadFile]=File(None)):
    ent=entreprise.strip().upper(); nom=modele.strip()
    if ent not in TEMPLATES_CONFIG or nom not in TEMPLATES_CONFIG[ent]: raise HTTPException(404,'Modèle introuvable.')
    try:
        zones = json.loads(zones_modifiables)
    except Exception as e:
        raise HTTPException(400,'Format des zones incorrect.') from e
    if not isinstance(zones, dict): raise HTTPException(400,'Les zones doivent être un objet JSON.')

    cfg=TEMPLATES_CONFIG[ent][nom]
    cfg['zones_modifiables']=zones
    if objectif_publication and objectif_publication.strip():
        cfg['objectif_publication']=objectif_publication.strip()
    for fichier,cle in [
        (base_reference,'base_reference'),
        (calque_fixe,'calque_fixe'),
    ]:
        if fichier and fichier.filename:
            cfg[cle]=upload_template_file(ent, nom, cle, await fichier.read())

    try:
        save_template_config(ent, nom, cfg)
    except Exception as e:
        raise HTTPException(500, f'Erreur pendant la sauvegarde du modèle : {e}') from e
    _save_config_snapshot()
    return {'status':'ok','config':cfg}

@app.get('/modeles/{entreprise}/{modele}')
def get_modele(entreprise:str,modele:str): return obtenir_modele(entreprise,modele,TEMPLATES_CONFIG)
@app.get('/modeles/{entreprise}/{modele}/fichier/{fichier}')
def get_modele_fichier(entreprise:str,modele:str,fichier:str):
    if fichier not in ['base_reference','calque_fixe']:
        raise HTTPException(400,'Fichier invalide.')

    chemin=obtenir_fichier_modele(entreprise,modele,fichier,TEMPLATES_CONFIG)
    try:
        data = download_template_file(chemin)
    except Exception as e:
        raise HTTPException(404,'Fichier introuvable.') from e

    return Response(content=data, media_type='image/png')
@app.get('/objectifs/{entreprise}')
def get_objectifs(entreprise:str): return lister_objectifs(entreprise,TEMPLATES_CONFIG)
@app.post('/generate-caption')
def caption(request:CaptionRequest): return generate_caption(request,COMPANY_PROFILES)
@app.post('/adapt-caption')
def adapt_caption_route(request:CaptionAdaptationRequest): return adapt_caption(request,COMPANY_PROFILES)
@app.post('/generate-image-ia')
def image_ia(data:dict): return generate_ai_image(data.get('prompt',''))
@app.post('/historique')
async def upload_historique(image: UploadFile = File(...)):
    data = await image.read()

    if not data:
        raise HTTPException(400, 'Image vide.')

    empreinte = hashlib.sha256(data).hexdigest()
    nom = f'{empreinte}.png'
    chemin = f'historique/{nom}'

    try:
        supabase.storage.from_('Flyers').upload(
            chemin,
            data,
            {"content-type": "image/png", "upsert": "false"}
        )
    except Exception as e:
        if 'already exists' not in str(e).lower():
            raise HTTPException(500, f'Erreur Supabase : {e}')

    cache.delete_prefix('historique:page:')
    return {'status': 'ok', 'nom': nom, 'chemin': chemin}


HISTORIQUE_CACHE_TTL = 600
HISTORIQUE_SIGNED_URL_TTL = 3600


@app.get('/historique')
def get_historique(page: int = Query(1, ge=1), page_size: int = Query(8, ge=1, le=24)):
    cache_key = f'historique:page:{page}:size:{page_size}'
    cached_page = cache.get_json(cache_key)
    if isinstance(cached_page, dict) and isinstance(cached_page.get('items'), list):
        return cached_page

    try:
        bucket = supabase.storage.from_('Flyers')
        fichiers = bucket.list(
            'historique',
            {
                'limit': page_size + 1,
                'offset': (page - 1) * page_size,
                'sortBy': {'column': 'created_at', 'order': 'desc'},
            },
        )

        noms = [
            fichier.get('name', '')
            for fichier in fichiers
            if fichier.get('name', '').lower().endswith('.png')
        ]
        has_next = len(noms) > page_size
        noms = noms[:page_size]
        def signer_miniature(nom):
            return bucket.create_signed_url(
                f'historique/{nom}',
                HISTORIQUE_SIGNED_URL_TTL,
                {'transform': {'width': 160, 'height': 120, 'resize': 'cover'}},
            )

        with ThreadPoolExecutor(max_workers=min(8, len(noms) or 1)) as executor:
            urls_signees = list(executor.map(signer_miniature, noms))
        result = [
            {
                'nom': noms[index],
                'thumbnail_url': url['signedURL'],
                'url': f"/historique/{noms[index]}/fichier",
            }
            for index, url in enumerate(urls_signees)
            if url.get('signedURL')
        ]

        result = {
            'items': result,
            'page': page,
            'page_size': page_size,
            'has_next': has_next,
        }
        cache.set_json(cache_key, result, ttl=HISTORIQUE_CACHE_TTL)
        return result

    except Exception as e:
        raise HTTPException(500,f'Erreur Supabase : {e}')

@app.get('/historique/{nom}/fichier')
def get_fichier_historique(nom: str):
    if not nom.lower().endswith('.png') or '/' in nom or '\\' in nom:
        raise HTTPException(400, 'Nom de fichier invalide.')

    try:
        contenu = supabase.storage.from_('Flyers').download(f'historique/{nom}')
    except Exception as e:
        raise HTTPException(404, 'Image historique introuvable.') from e

    return Response(content=contenu, media_type='image/png')

@app.post('/preview')
async def preview_flyer(
    entreprise: str = Form(...),
    modele: str = Form(...),
    langue: str = Form('Français'),
    valeurs: str = Form('{}'),
    fontes: str = Form('{}'),
    couleur_filtre: str = Form("#3F257C"),
    opacite_filtre: int = Form(0, ge=0, le=100),
    image_fond: Optional[UploadFile] = File(None)
):
    ent = entreprise.strip().upper()

    if ent not in TEMPLATES_CONFIG:
        raise HTTPException(400, 'Entreprise inconnue.')

    if modele not in TEMPLATES_CONFIG[ent]:
        raise HTTPException(400, 'Modèle inconnu.')

    try:
        values = json.loads(valeurs)
    except Exception:
        raise HTTPException(400, 'Format des valeurs incorrect.')

    raw = await image_fond.read() if image_fond and image_fond.filename else None
    if not raw:
        raise HTTPException(400, 'Choisissez ou importez une image avant la previsualisation.')

    try:
        fonts = json.loads(fontes)
    except Exception:
        raise HTTPException(400, 'Format des polices incorrect.')

    if not isinstance(fonts, dict):
        raise HTTPException(400, 'Les polices doivent être un objet JSON.')
    if not isinstance(values, dict):
        raise HTTPException(400, 'Les valeurs doivent être un objet JSON.')

    
    return generate(
        TEMPLATES_CONFIG[ent][modele],
        values,
        raw,
        ent,
        modele,
        None if langue == 'Français' else langue,
        preview=True,
        fontes=fonts,
        couleur_filtre=couleur_filtre,
        opacite_filtre=opacite_filtre
    )

@app.post('/generate')
async def generate_flyer(entreprise:str=Form(...),modele:str=Form(...),langue:str=Form('Français'),valeurs:str=Form('{}'),fontes: str = Form('{}'), couleur_filtre:str=Form("#3F257C"), opacite_filtre:int=Form(0, ge=0, le=100), image_fond:Optional[UploadFile]=File(None)):
    ent=entreprise.strip().upper()
    if ent not in TEMPLATES_CONFIG: raise HTTPException(400,'Entreprise inconnue.')
    if modele not in TEMPLATES_CONFIG[ent]: raise HTTPException(400,'Modèle inconnu.')
    try: values=json.loads(valeurs)
    except Exception: raise HTTPException(400,'Format des valeurs incorrect.')
    if not isinstance(values,dict): raise HTTPException(400,'Les valeurs doivent être un objet JSON.')
    try:
        fonts = json.loads(fontes)
    except Exception:
        raise HTTPException(400, 'Format des polices incorrect.')

    if not isinstance(fonts, dict):
        raise HTTPException(400, 'Les polices doivent être un objet JSON.')
    raw=await image_fond.read() if image_fond and image_fond.filename else None
    if not raw: raise HTTPException(400,'Choisissez ou importez une image avant la generation.')
    return generate(TEMPLATES_CONFIG[ent][modele],values,raw,ent,modele, None if langue=='Français' else langue,fontes=fonts,couleur_filtre=couleur_filtre,opacite_filtre=opacite_filtre)

