"""
Cache Redis pour le backend.

Principe : Redis est une OPTION D'ACCÉLÉRATION, jamais une dépendance.
Si Redis est absent ou tombe, toutes les fonctions se comportent comme un cache
vide (get -> None, set -> ne fait rien) et l'application continue de fonctionner,
simplement plus lentement.

Variables d'environnement (.env) :
    REDIS_URL       ex. redis://localhost:6379/0   (rediss://... pour un Redis managé type Upstash)
    CACHE_ENABLED   "0" pour désactiver complètement le cache
    CACHE_PREFIX    préfixe des clés (défaut "flyers:")
"""
import json
import logging
import os
import threading
import time

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("cache")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "1") != "0"
PREFIX = os.getenv("CACHE_PREFIX", "flyers:")
PAUSE_APRES_ERREUR = 30  # secondes sans retenter Redis après une panne

_client = None
_pause_jusqua = 0.0
_verrou = threading.Lock()

# Compteurs locaux utilisés seulement quand Redis est indisponible
_compteurs_locaux = {}


def _redis():
    """Retourne le client Redis, ou None s'il est indisponible (disjoncteur)."""
    global _client, _pause_jusqua
    if not CACHE_ENABLED:
        return None
    if _client is not None:
        return _client
    if time.time() < _pause_jusqua:
        return None
    with _verrou:
        if _client is not None:
            return _client
        try:
            import redis  # import tardif : ne ralentit pas le démarrage si le cache est désactivé

            client = redis.Redis.from_url(
                REDIS_URL,
                socket_connect_timeout=0.5,
                socket_timeout=1.0,
                health_check_interval=30,
            )
            client.ping()
            _client = client
            log.info("Cache Redis connecté (%s)", REDIS_URL.split("@")[-1])
        except Exception as e:
            _pause_jusqua = time.time() + PAUSE_APRES_ERREUR
            log.warning("Redis indisponible, cache désactivé pour %ss : %s", PAUSE_APRES_ERREUR, e)
            return None
    return _client


def _panne(e):
    global _client, _pause_jusqua
    _client = None
    _pause_jusqua = time.time() + PAUSE_APRES_ERREUR
    log.warning("Erreur Redis, cache en pause %ss : %s", PAUSE_APRES_ERREUR, e)


def _k(cle):
    return PREFIX + cle


def actif():
    """True si Redis répond en ce moment."""
    return _redis() is not None


# ---------- octets ----------

def get_bytes(cle):
    r = _redis()
    if r is None:
        return None
    try:
        return r.get(_k(cle))
    except Exception as e:
        _panne(e)
        return None


def set_bytes(cle, valeur, ttl=3600):
    r = _redis()
    if r is None or valeur is None:
        return False
    try:
        r.set(_k(cle), valeur, ex=ttl)
        return True
    except Exception as e:
        _panne(e)
        return False


# ---------- JSON ----------

def get_json(cle):
    brut = get_bytes(cle)
    if brut is None:
        return None
    try:
        return json.loads(brut)
    except Exception:
        return None


def set_json(cle, valeur, ttl=3600):
    try:
        return set_bytes(cle, json.dumps(valeur, ensure_ascii=False).encode("utf-8"), ttl)
    except Exception:
        return False


# ---------- suppression / versions ----------

def delete(*cles):
    r = _redis()
    if r is None or not cles:
        return
    try:
        r.delete(*[_k(c) for c in cles])
    except Exception as e:
        _panne(e)


def delete_prefix(prefixe):
    r = _redis()
    if r is None:
        return
    try:
        lot = list(r.scan_iter(match=_k(prefixe) + "*", count=200))
        if lot:
            r.delete(*lot)
    except Exception as e:
        _panne(e)


def incr(cle):
    """Incrémente un compteur (sert de numéro de version pour invalider des caches)."""
    r = _redis()
    if r is not None:
        try:
            return int(r.incr(_k(cle)))
        except Exception as e:
            _panne(e)
    _compteurs_locaux[cle] = _compteurs_locaux.get(cle, 0) + 1
    return _compteurs_locaux[cle]


def get_int(cle):
    r = _redis()
    if r is not None:
        try:
            v = r.get(_k(cle))
            return int(v) if v is not None else 0
        except Exception as e:
            _panne(e)
    return _compteurs_locaux.get(cle, 0)
