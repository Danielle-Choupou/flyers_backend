import os, re
from typing import Optional
from fastapi import HTTPException
from groq import Groq
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

class CaptionRequest(BaseModel):
    entreprise: str
    template_type: str
    langue: str = "Français"
    titre_principal: Optional[str] = None
    poste: Optional[str] = None
    lieu: Optional[str] = None
    description: Optional[str] = None
    instruction_utilisateur: Optional[str] = None

def build_system_prompt(entreprise, profiles, template_type):
    profil = profiles.get(entreprise)
    if not profil:
        raise HTTPException(400, f"Aucun profil éditorial pour {entreprise}")
    charte = profil.get("charte_editoriale", {})
    regles = charte.get("regles_strictes", {})
    piliers = profil.get("piliers_culturels", {})
    lexique = profil.get("lexique_et_seo", {})
    marque = profil.get("elements_de_marque", {})
    cibles = profil.get("public_cible", {})
    ancrage = profil.get("ancrage_local", {})
    if profil.get("expertises_techniques"):
        activites = ", ".join(profil["expertises_techniques"].keys())
    elif profil.get("divisions_et_marques"):
        marques = [m for d in profil["divisions_et_marques"].values() for m in d.get("marques_cles", [])]
        activites = ", ".join(marques[:6])
    else: activites = ""
    localisation = ancrage.get("pays_operation") or ", ".join(ancrage.get("reseau_agences", [])[:3])
    points_forts = list(dict.fromkeys(piliers.get("valeurs", []) + piliers.get("arguments_recrutement", [])))[:5]
    cible = ", ".join((cibles.get("recrutement") or cibles.get("clients") or [])[:4])
    signature = marque.get("signature_institutionnelle") or marque.get("signature_employeur", "")
    interdictions = "; ".join(regles.get("interdictions", [])[:2])
    consigne_langue = charte.get("consigne_multilingue", "")
    emojis = "emojis modérés OK" if regles.get("emojis") else "sans emoji"
    hashtags = "2-3 hashtags en fin" if regles.get("hashtags") else "sans hashtag"
    prompt = f'''Community manager de {profil.get("nom_officiel", entreprise)} ({profil.get("secteur_activite", "").split(",")[0]}).
Activités clés: {activites}
{f"Zone: {localisation}" if localisation else ""}
Ton: {charte.get("style", "professionnel")}, formalité {charte.get("niveau_formalite", "moyenne")}, {emojis}, {hashtags}
{f"Interdit: {interdictions}" if interdictions else ""}
Cible: {cible}
À valoriser: {", ".join(points_forts)}
Vocabulaire: privilégier [{", ".join(lexique.get("vocabulaire_favorise", [])[:4])}], éviter [{", ".join(lexique.get("termes_a_eviter", [])[:3])}]
{consigne_langue}

Écris un texte d'accompagnement (3-5 phrases) pour un flyer "{template_type}", destiné aux réseaux sociaux. Termine par la signature "{signature}" si adapté. Jamais mentionner être une IA. Réponds uniquement avec le texte, sans guillemets. IMPORTANT :
Ne montre jamais ton raisonnement.
Ne produis jamais de balises <think>.

Réponds uniquement avec le texte final.'''
    return re.sub(r"\n{2,}", "\n", prompt).strip()

def nettoyer_reasoning(texte):
    if "<think>" in texte:
        texte = re.sub(r"<think>.*?</think>", "", texte, flags=re.DOTALL) if "</think>" in texte else texte.split("<think>")[0]
    if "Text:" in texte: texte = texte.split("Text:")[-1]
    return texte.strip()

def traduire_texte(texte, langue):
    if not texte or langue == "Français": return texte
    if not GROQ_API_KEY: raise HTTPException(500, "Clé GROQ_API_KEY manquante pour la traduction.")
    prompt = f'''Traduis le texte suivant en {langue}.
Règles :
- retourne uniquement la traduction
- aucun commentaire
- aucun guillemet
- conserve les noms propres, noms d'entreprise, marques, villes, URL et numéros de téléphone
- reste naturel et professionnel

Texte :
{texte}'''
    try:
        r = Groq(api_key=GROQ_API_KEY).chat.completions.create(model="qwen/qwen3.8-27b", messages=[{"role":"user","content":prompt}], temperature=0.1, reasoning_effort="none", reasoning_format="hidden", max_completion_tokens=150)
        return nettoyer_reasoning(r.choices[0].message.content)
    except Exception as e: raise HTTPException(500, f"Erreur traduction : {e}")

def generate_caption(request, profiles):
    if not GROQ_API_KEY: raise HTTPException(500, "Clé GROQ_API_KEY manquante sur le serveur.")
    entreprise = request.entreprise.strip().upper()
    system_prompt = build_system_prompt(entreprise, profiles, request.template_type)
    contexte = f'''Type: {request.template_type}
Langue demandée: {request.langue}
Titre: {request.titre_principal or "-"}
Poste: {request.poste or "-"}
Lieu: {request.lieu or "-"}
Description: {request.description or "-"}
LANGUE : Le français est toujours obligatoire. La langue choisie est : {request.langue}.
Si la langue choisie est Français, écris uniquement en français.
Sinon, écris d'abord la version française, puis la version dans la langue choisie. Les deux versions doivent transmettre exactement les mêmes informations.'''
    if request.instruction_utilisateur and request.instruction_utilisateur.strip(): contexte += f"\n\nINSTRUCTION SPÉCIFIQUE DE L'UTILISATEUR :\n{request.instruction_utilisateur.strip()}"
    try:
        r = Groq(api_key=GROQ_API_KEY).chat.completions.create(model="qwen/qwen3.8-27b", messages=[{"role":"system","content":system_prompt},{"role":"user","content":contexte}], temperature=0.5, reasoning_effort="none", reasoning_format="hidden", max_completion_tokens=400)
        return {"caption": nettoyer_reasoning(r.choices[0].message.content)}
    except Exception as e: raise HTTPException(500, str(e))
