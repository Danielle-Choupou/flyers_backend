import json
from typing import Optional
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from services.entreprises import lister_entreprises, obtenir_entreprise, ajouter_entreprise, modifier_entreprise, supprimer_entreprise
from services.modeles import ajouter_modele, obtenir_modele, lister_modeles, lister_objectifs, save_uploaded_template_files, obtenir_fichier_modele
from services.images import  generate_ai_image
from services.IA import CaptionRequest, generate_caption
from services.generation_flyer import generate, google_fonts
from config import TEMPLATES_DIR
from fastapi.responses import FileResponse
from services.storage import load_configs, save_configs, supabase
from functools import lru_cache
import hashlib

app=FastAPI(title="Générateur de Flyers - Backend")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
COMPANY_PROFILES,TEMPLATES_CONFIG=load_configs()

def reload():
    global COMPANY_PROFILES,TEMPLATES_CONFIG
    COMPANY_PROFILES,TEMPLATES_CONFIG=load_configs()

@app.get('/health')
def health(): return {'status':'ok','message':'Backend opérationnel'}
@app.get('/fonts')
@lru_cache(maxsize=1)
def get_google_fonts():
    result={}
    for nom,font in google_fonts().items():
        variants=font.get("files",{})
        weights=set()
        styles=set()
        for v in variants:
            style="italic" if v.endswith("italic") else "normal"
            poids=v.replace("italic","")
            weights.add(400 if poids in ("","regular") else int(poids))
            styles.add(style)
        result[nom]={"weights":sorted(weights),"styles":sorted(styles)}
    return result

@app.get('/fonts')
def get_fonts():
    return get_google_fonts()

@app.post('/reload-config')
def reload_config():
    reload(); return {'status':'ok','entreprises_templates':list(TEMPLATES_CONFIG),'entreprises_profils':list(COMPANY_PROFILES)}
@app.get('/entreprises')
def get_entreprises(): return lister_entreprises(TEMPLATES_CONFIG)
@app.get('/entreprises/{nom}')
def get_entreprise(nom:str): return obtenir_entreprise(nom,COMPANY_PROFILES)
@app.post('/entreprises')
def post_entreprise(data:dict): return ajouter_entreprise(data,COMPANY_PROFILES,TEMPLATES_CONFIG)
@app.put('/entreprises/{nom}')
def put_entreprise(nom:str,data:dict): return modifier_entreprise(nom,data,COMPANY_PROFILES,TEMPLATES_CONFIG)
@app.delete('/entreprises/{nom}')
def delete_entreprise(nom:str): return supprimer_entreprise(nom,COMPANY_PROFILES,TEMPLATES_CONFIG)
@app.post('/modeles')
def post_modele(data:dict): return ajouter_modele(data,TEMPLATES_CONFIG,COMPANY_PROFILES)
@app.get('/modeles/{entreprise}')
def get_modeles(entreprise:str): return lister_modeles(entreprise,TEMPLATES_CONFIG)
@app.post('/modeles/complet')
async def post_modele_complet(entreprise:str=Form(...),modele:str=Form(...),objectif_publication:str=Form(...),base_reference:UploadFile=File(...),calque_fixe:UploadFile=File(...),fond_defaut:UploadFile=File(...)):
    ent=entreprise.strip().upper(); nom=modele.strip(); obj=objectif_publication.strip()
    if ent not in TEMPLATES_CONFIG: raise HTTPException(404,'Entreprise introuvable.')
    if not nom: raise HTTPException(400,'Nom du modèle obligatoire.')
    if not obj: raise HTTPException(400,'Objectif de publication obligatoire.')
    if nom in TEMPLATES_CONFIG[ent]: raise HTTPException(400,'Ce modèle existe déjà.')
    paths=save_uploaded_template_files(ent,nom,await base_reference.read(),await calque_fixe.read(),await fond_defaut.read())
    # La configuration des zones est envoyée séparément par le frontend après l'éditeur.
    cfg={'base_reference':paths['base_reference'],'calque_fixe':paths['calque_fixe'],'fond_defaut':paths['fond_defaut'],'zones_modifiables':{}}
    TEMPLATES_CONFIG[ent][nom]=cfg; TEMPLATES_CONFIG[ent][nom]['objectif_publication']=obj
    from services.storage import save_configs
    save_configs(COMPANY_PROFILES,TEMPLATES_CONFIG)
    return {'status':'ok','entreprise':ent,'modele':nom,'objectif_publication':obj,'config':cfg}

@app.put('/modeles/{entreprise}/{modele}')
def put_modele(entreprise:str,modele:str,data:dict):
    ent=entreprise.strip().upper(); nom=modele.strip()
    if ent not in TEMPLATES_CONFIG or nom not in TEMPLATES_CONFIG[ent]: raise HTTPException(404,'Modèle introuvable.')
    cfg=TEMPLATES_CONFIG[ent][nom]; cfg['zones_modifiables']=data.get('zones_modifiables',cfg.get('zones_modifiables',{}))
    if data.get('objectif_publication'): cfg['objectif_publication']=data['objectif_publication']
    from services.storage import save_configs
    save_configs(COMPANY_PROFILES,TEMPLATES_CONFIG)
    return {'status':'ok','config':cfg}
@app.put('/modeles/{entreprise}/{modele}/fichiers')
async def put_modele_fichiers(entreprise:str,modele:str,base_reference:Optional[UploadFile]=File(None),calque_fixe:Optional[UploadFile]=File(None),fond_defaut:Optional[UploadFile]=File(None)):
    ent=entreprise.strip().upper(); nom=modele.strip()
    if ent not in TEMPLATES_CONFIG or nom not in TEMPLATES_CONFIG[ent]: raise HTTPException(404,'Modèle introuvable.')
    if not any([base_reference,calque_fixe,fond_defaut]): raise HTTPException(400,'Aucun fichier à remplacer.')

    cfg=TEMPLATES_CONFIG[ent][nom]
    dossier=TEMPLATES_DIR/ent
    dossier.mkdir(parents=True,exist_ok=True)

    for fichier,cle,suffixe in [
        (base_reference,'base_reference','_base.png'),
        (calque_fixe,'calque_fixe','_overlay.png'),
        (fond_defaut,'fond_defaut','_fond.png')
    ]:
        if fichier and fichier.filename:
            chemin=dossier/f'{nom}{suffixe}'
            chemin.write_bytes(await fichier.read())
            cfg[cle]=str(chemin)

    save_configs(COMPANY_PROFILES,TEMPLATES_CONFIG)
    return {'status':'ok','config':cfg}

@app.get('/modeles/{entreprise}/{modele}')
def get_modele(entreprise:str,modele:str): return obtenir_modele(entreprise,modele,TEMPLATES_CONFIG)
@app.get('/modeles/{entreprise}/{modele}/fichier/{fichier}')
def get_modele_fichier(entreprise:str,modele:str,fichier:str):
    if fichier not in ['base_reference','calque_fixe','fond_defaut']:
        raise HTTPException(400,'Fichier invalide.')

    chemin=obtenir_fichier_modele(entreprise,modele,fichier,TEMPLATES_CONFIG)

    if not chemin.exists():
        raise HTTPException(404,'Fichier introuvable.')

    return FileResponse(chemin)
@app.get('/objectifs/{entreprise}')
def get_objectifs(entreprise:str): return lister_objectifs(entreprise,TEMPLATES_CONFIG)
@app.post('/generate-caption')
def caption(request:CaptionRequest): return generate_caption(request,COMPANY_PROFILES)
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

    return {'status': 'ok', 'nom': nom, 'chemin': chemin}
@app.get('/historique')
@app.get('/historique')
def get_historique():
    try:
        fichiers=supabase.storage.from_('Flyers').list('historique')

        result=[]

        for f in fichiers:
            nom=f.get('name','')

            if nom.lower().endswith('.png'):
                url=supabase.storage.from_('Flyers').create_signed_url(
                    f'historique/{nom}',
                    3600
                )

                result.append({
                    'nom':nom,
                    'url':url['signedURL']
                })

        return result

    except Exception as e:
        raise HTTPException(500,f'Erreur Supabase : {e}')

@app.post('/preview')
async def preview_flyer(
    entreprise: str = Form(...),
    modele: str = Form(...),
    langue: str = Form('Français'),
    valeurs: str = Form('{}'),
    fontes: str = Form('{}'),
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

    raw = (
            await image_fond.read()
            if image_fond and image_fond.filename
            else None
        )
    
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
        fontes=fonts
    )

@app.post('/generate')
async def generate_flyer(entreprise:str=Form(...),modele:str=Form(...),langue:str=Form('Français'),valeurs:str=Form('{}'),fontes: str = Form('{}'), image_fond:Optional[UploadFile]=File(None)):
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
    return generate(TEMPLATES_CONFIG[ent][modele],values,raw,ent,modele, None if langue=='Français' else langue,fontes=fonts)

