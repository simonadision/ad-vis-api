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
from fastapi import Header, HTTPException, Query
from psycopg.rows import dict_row

JWT_ALGORITHM = "HS256"
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


def _extract_bearer(authorization: Optional[str], token_query: Optional[str] = None) -> str:
    """Lit le JWT du header `Authorization: Bearer <jwt>` ou du query `?token=`."""
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip()
        raise HTTPException(status_code=401, detail="Header Authorization mal formé (attendu : Bearer <JWT>)")
    if token_query:
        return token_query.strip()
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


def make_jwt_deps(get_conn):
    """Crée les FastAPI dependencies bound au get_conn de l'app."""

    def jwt_user(
        authorization: Optional[str] = Header(None),
        token: Optional[str] = Query(None),
    ) -> dict:
        """Standard : JWT via header ou `?token=`. Vérifie signature, AUCUN
        gating de module (souple Sprint 0), auto-provisionne le user, retourne
        le row local enrichi (organization_id, platform_role, modules)."""
        jwt_token = _extract_bearer(authorization, token)
        payload = _decode_token(jwt_token)
        # GATING SOUPLE — pas de _check_module ici (cf. docstring module).
        conn = get_conn()
        try:
            user = _provision_user(conn, payload)
        finally:
            conn.close()
        user["modules"] = payload.get("modules") or []
        # Workspace Switcher (035) : active_organization_id prime sur le legacy.
        user["organization_id"] = (
            payload.get("active_organization_id") or payload.get("organization_id")
        )
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
    ) -> dict:
        """Endpoints d'administration plateforme (super_admin). Pas de
        provisioning local ; retourne le payload JWT."""
        jwt_token = _extract_bearer(authorization, token)
        payload = _decode_token(jwt_token)
        platform_role = (
            payload.get("platform_role") or _derive_platform_role(payload.get("role"))
        )
        if platform_role != "super_admin":
            raise HTTPException(status_code=403, detail="Accès super_admin requis")
        return payload

    return jwt_user, jwt_super_admin
