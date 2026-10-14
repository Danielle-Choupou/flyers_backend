import json
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


class CaptionAdaptationRequest(BaseModel):
    entreprise: str
    template_type: str
    langue: str = "Français"
    texte_source: str
    reseaux: list[str]

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


def _regles_reseau(profil, reseau):
    regles = (profil.get("regles_reseaux") or {}).get(reseau)
    if not isinstance(regles, dict):
        raise HTTPException(400, f"Les règles de {reseau} sont absentes du profil Supabase de {profil.get('nom_officiel', 'cette entreprise')}.")
    if "max_caracteres" not in regles:
        raise HTTPException(400, f"La limite max_caracteres de {reseau} est absente du profil Supabase.")
    try:
        regles = {**regles, "max_caracteres": int(regles["max_caracteres"])}
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"La limite max_caracteres de {reseau} dans Supabase doit être un nombre entier.") from exc
    if regles["max_caracteres"] <= 0:
        raise HTTPException(400, f"La limite max_caracteres de {reseau} dans Supabase doit être positive.")
    return regles


def _adaptation_prompt(entreprise, profiles, template_type, langue, reseau):
    profil = profiles.get(entreprise)
    if not profil:
        raise HTTPException(400, f"Aucun profil éditorial pour {entreprise}")
    regles = _regles_reseau(profil, reseau)
    profil_json = json.dumps(profil, ensure_ascii=False, indent=2)
    regles_json = json.dumps(regles, ensure_ascii=False, indent=2)
    return f"""Tu es le community manager de {profil.get('nom_officiel', entreprise)}.
Contexte du flyer : {template_type}.
Langue de sortie : {langue}.

Voici toutes les informations éditoriales validées pour cette entreprise, depuis son profil Supabase :
{profil_json}

Voici les règles validées pour la plateforme {reseau} (elles priment pour les contraintes propres à cette plateforme) :
{regles_json}

Adapte le texte source pour {reseau} en appliquant le profil complet de l'entreprise : identité, secteur, historique, rôle, ton, niveau de formalité, cible, objectifs, valeurs, vocabulaire recommandé, termes à éviter, règles strictes, informations locales et toutes les autres consignes pertinentes présentes dans le profil.
Respecte impérativement la structure et la longueur idéale de la plateforme si elles sont définies. La limite absolue est de {regles['max_caracteres']} caractères, espaces compris.
Respecte le nombre de hashtags et l'usage des emojis définis par l'entreprise et la plateforme.
N'ajoute aucun fait, chiffre, disponibilité, promesse, adresse ou lien qui n'apparait pas dans le texte source ou dans les informations validées du profil. Si une information nécessaire manque, applique la consigne mention_manquante du profil lorsqu'elle existe.
Ne génère pas une nouvelle campagne : reformule et ajuste uniquement le contenu fourni.
Ne mentionne jamais l'IA, ne montre pas ton raisonnement, et retourne uniquement le caption final sans guillemets ni préambule."""


def adapt_caption(request, profiles):
    if not GROQ_API_KEY:
        raise HTTPException(500, "Clé GROQ_API_KEY manquante sur le serveur.")

    entreprise = request.entreprise.strip().upper()
    texte_source = request.texte_source.strip()
    reseaux = list(dict.fromkeys(request.reseaux))
    if entreprise not in profiles:
        raise HTTPException(404, f"Entreprise introuvable : {entreprise}")
    if not texte_source:
        raise HTTPException(400, "Le texte source est obligatoire.")
    if not reseaux:
        raise HTTPException(400, "Sélectionnez au moins un réseau social.")

    adaptations = {}
    try:
        for reseau in reseaux:
            prompt = f"""Texte source fourni par l'utilisateur :
{texte_source}

Adapte ce texte pour {reseau}. Respecte impérativement la limite de caractères et retourne uniquement la version finale."""
            limite = _regles_reseau(profiles[entreprise], reseau)["max_caracteres"]
            system_prompt = _adaptation_prompt(entreprise, profiles, request.template_type, request.langue, reseau)
            client = Groq(api_key=GROQ_API_KEY)
            texte = ""
            for tentative in range(2):
                consigne = prompt
                if tentative:
                    consigne += f"\n\nIMPORTANT : ta réponse précédente dépassait la limite. Réécris-la plus courte, en moins de {limite} caractères, sans perdre les informations essentielles."
                response = client.chat.completions.create(
                    model="qwen/qwen3.8-27b",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": consigne},
                    ],
                    temperature=0.35,
                    reasoning_effort="none",
                    reasoning_format="hidden",
                    max_completion_tokens=700,
                )
                texte = nettoyer_reasoning(response.choices[0].message.content)
                if len(texte) <= limite:
                    break
            if len(texte) > limite:
                raise HTTPException(502, f"Le caption {reseau} dépasse encore la limite de {limite} caractères après une nouvelle tentative.")
            adaptations[reseau] = {"texte": texte, "caracteres": len(texte), "maximum": limite}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Erreur d'adaptation pour {reseau} : {e}") from e

    return {"adaptations": adaptations}
