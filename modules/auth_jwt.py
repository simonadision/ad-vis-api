"""
Vérification du JWT SSO Adision côté backend Ad VIS (visite de chantier).

Le JWT est émis par le dashboard adision-app-api (HS256, secret partagé via
JWT_SECRET). Ad VIS ne fait PAS de login : il vérifie la signature et
auto-provisionne le user dans ad_vis.users à sa première connexion SSO.

GATING SOUPLE (Sprint 0) — décision Simon : on N'EXIGE PAS le module 'ad_vis'
dans le claim `modules` du JWT. Tout utilisateur authentifié de la suite peut
entrer dans Ad VIS le temps du Sprint 0. Le vrai octroi de module (claim
`modules` + flag/grant côté HUB) sera câblé quand Ad VIS sortira du Sprint 0.
Pour le réactiver : remettre le check `_check_module` dans `jwt_user`.

Le user_id retourné par le dependency est le `ad_vis.users.id` LOCAL — utilisé
comme FK dans `ad_vis.visites.auteur_user_id`.
"""
import os
from typing import Optional

import jwt
from fastapi import Cookie, Header, HTTPException, Query

from modules.revocation_cache import is_token_revoked
from psycopg.rows import dict_row

JWT_ALGORITHM = "HS256"

# Étape 1 SSO (juin 2026) — cookie de session cross-subdomain ADDITIF.
# Lu en dernier ressort par `_extract_bearer`. Posé par adision-app-api
# au login. Si absent, le système header/query reste fonctionnel.
SESSION_COOKIE_NAME = "__Secure-adision-session"
# Sprint 0 souple : conservé pour le jour où on réactive le gating.
REQUIRED_MODULE = "ad_vis"


def _get_jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail="JWT_SECRET non configuré côté serveur")
    return secret


def _decode_token(token: str) -> dict:
    """Vérifie signature + expiration. Rotation gracieuse via JWT_SECRET_OLD
    (seul un échec de SIGNATURE déclenche le repli ; expiré/malformé non)."""
    try:
        try:
            return jwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        except jwt.InvalidSignatureError:
            old = os.environ.get("JWT_SECRET_OLD", "")
            if old:
                return jwt.decode(token, old, algorithms=[JWT_ALGORITHM])
            raise
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expiré, reconnexion nécessaire")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Token invalide : {e}")


def _derive_platform_role(role):
    if role == "super_admin":
        return "super_admin"
    if role == "admin":
        return "staff"
    return "client"


def _extract_bearer(
    authorization: Optional[str],
    token_query: Optional[str] = None,
    session_cookie: Optional[str] = None,
) -> str:
    """Lit le JWT du header `Authorization: Bearer <jwt>`, du query `?token=`
    ou du cookie de session cross-subdomain (étape 1 SSO, juin 2026)."""
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip()
        raise HTTPException(status_code=401, detail="Header Authorization mal formé (attendu : Bearer <JWT>)")
    if token_query:
        return token_query.strip()
    if session_cookie:
        return session_cookie.strip()
    raise HTTPException(status_code=401, detail="Authentification requise (header Authorization: Bearer <JWT>)")


def _provision_user(conn, payload: dict) -> dict:
    """Lookup user dans ad_vis.users par email ; auto-provision si absent
    (premier login SSO). Lève 403 si compte désactivé."""
    email = (payload.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=401, detail="JWT sans email")

    cur = conn.cursor(row_factory=dict_row)
    cur.execute(
        "SELECT id, email, nom, role, actif, created_at, last_login_at "
        "FROM ad_vis.users WHERE LOWER(email) = %s",
        (email,),
    )
    row = cur.fetchone()
    if not row:
        nom = (payload.get("nom") or email.split("@", 1)[0]).strip() or email
        role = payload.get("role") or "user"
        cur.execute(
            "INSERT INTO ad_vis.users (email, nom, role) VALUES (%s, %s, %s) "
            "RETURNING id, email, nom, role, actif, created_at, last_login_at",
            (email, nom, role),
        )
        row = cur.fetchone()
        conn.commit()
    cur.close()

    user = dict(row)
    jwt_role = (payload.get("role") or "").strip()
    if jwt_role:
        user["role"] = jwt_role  # le JWT (dashboard) est la source de vérité du rôle
    if not user["actif"]:
        raise HTTPException(status_code=403, detail="Compte désactivé")
    return user


# ── Garde-fou : allowlist d'organisations (effectif, réversible) ─────────
# Le gating de MODULE reste souple (pas de check du claim 'ad_vis'), MAIS tant
# que le resserrement Sprint 1 n'est pas câblé côté HUB, Ad VIS n'est ouvert
# qu'aux orgs de cette liste — toute autre org reçoit 403 sur TOUS les endpoints
# (la tuile seule serait cosmétique : l'URL directe resterait atteignable).
# Override : env AD_VIS_ORG_ALLOWLIST (UUIDs séparés par virgules) ; '*' (ou vide)
# DÉSACTIVE le garde-fou (à poser au Sprint 1 quand on ouvre plus large).
_DEFAULT_ALLOWLIST = (
    "870c8388-4b9c-4ab6-b8e6-bbc8410638c3,"  # Adision
    "64c45c53-ff2b-40ce-b05d-fe0a3f5144c0"   # Contracta
)


def _org_allowlist():
    raw = os.environ.get("AD_VIS_ORG_ALLOWLIST", _DEFAULT_ALLOWLIST).strip()
    if raw in ("*", ""):
        return None  # None = aucune restriction (ouvert à toutes les orgs)
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


def make_jwt_deps(get_conn):
    """Crée les FastAPI dependencies bound au get_conn de l'app."""

    def jwt_user(
        authorization: Optional[str] = Header(None),
        token: Optional[str] = Query(None),
        session_cookie: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
    ) -> dict:
        """Standard : JWT via header, `?token=` ou cookie de session (étape 1 SSO).
        Vérifie signature, AUCUN gating de module (souple Sprint 0),
        auto-provisionne le user, retourne le row local enrichi."""
        jwt_token = _extract_bearer(authorization, token, session_cookie)
        payload = _decode_token(jwt_token)
        # Phase J3 étape 3 — kill-switch satellite (cat.2, endpoint hub
        # + cache TTL 60s). Voir modules/revocation_cache.py. Fail-open
        # défensif si erreur (ne propage jamais de 500).
        if is_token_revoked(payload.get("user_id"), payload.get("iat")):
            raise HTTPException(
                status_code=401,
                detail="Session révoquée — reconnexion requise",
            )
        # Workspace Switcher (035) : active_organization_id prime sur le legacy.
        org_id = payload.get("active_organization_id") or payload.get("organization_id")
        # Garde-fou allowlist d'orgs — AVANT le provisioning (aucune row ad_vis.users
        # créée pour une org non autorisée). S'applique à TOUS les endpoints (/auth/me
        # inclus). GATING SOUPLE module conservé (pas de _check_module).
        allow = _org_allowlist()
        if allow is not None and str(org_id or "").lower() not in allow:
            raise HTTPException(
                status_code=403,
                detail="Ad VIS n'est pas activé pour votre organisation",
            )
        # DOUBLE FILET (migration 089) : flag PER-USER Ad VIS via le claim JWT `modules`
        # (dérivé de users.has_ad_vis). L'org reste le plafond (allowlist ci-dessus) ;
        # ce flag décide par utilisateur. NB : un token émis AVANT 089 n'a pas 'ad_vis'
        # dans modules → 403 jusqu'à la prochaine connexion (re-login régénère le claim).
        if REQUIRED_MODULE not in (payload.get("modules") or []):
            raise HTTPException(
                status_code=403,
                detail="Ad VIS n'est pas activé pour cet utilisateur",
            )
        conn = get_conn()
        try:
            user = _provision_user(conn, payload)
        finally:
            conn.close()
        user["modules"] = payload.get("modules") or []
        user["organization_id"] = org_id
        user["platform_role"] = (
            payload.get("platform_role") or _derive_platform_role(payload.get("role"))
        )
        user["org_role"] = payload.get("active_role") or payload.get("org_role")
        # Token brut conservé pour les appels cross-service (proxy HUB).
        user["_jwt"] = jwt_token
        return user

    def jwt_super_admin(
        authorization: Optional[str] = Header(None),
        token: Optional[str] = Query(None),
        session_cookie: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
    ) -> dict:
        """Endpoints d'administration plateforme (super_admin). Pas de
        provisioning local ; retourne le payload JWT."""
        jwt_token = _extract_bearer(authorization, token, session_cookie)
        payload = _decode_token(jwt_token)
        # Phase J3 étape 3 — kill-switch satellite (cat.2). Un super_admin
        # révoqué est révoqué partout, donc check identique à jwt_user.
        if is_token_revoked(payload.get("user_id"), payload.get("iat")):
            raise HTTPException(
                status_code=401,
                detail="Session révoquée — reconnexion requise",
            )
        platform_role = (
            payload.get("platform_role") or _derive_platform_role(payload.get("role"))
        )
        if platform_role != "super_admin":
            raise HTTPException(status_code=403, detail="Accès super_admin requis")
        return payload

    return jwt_user, jwt_super_admin
