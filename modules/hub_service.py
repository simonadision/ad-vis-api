"""
Hub Service — client HTTP cross-service Ad VIS → Ad HUB (adision-app-api).

Ad VIS lit la liste des projets de l'org depuis le HUB en PROPAGEANT le JWT
user (Authorization: Bearer …). Le HUB valide la signature + l'organization_id
(anti cross-tenant) — Ad VIS n'a donc pas à dupliquer le contrôle d'accès.

Pattern identique à adision-viu-api/modules/hub_service.py (_hub_get).
HUB_API_URL : env var, fallback = URL publique HTTPS (DNS interne Railway
`*.railway.internal` indisponible cross-projet).
"""
import os
from typing import Optional

import httpx

HUB_API_URL = os.environ.get(
    "HUB_API_URL",
    "https://web-production-1c52e.up.railway.app",
)
HUB_TIMEOUT_S = 10.0


class HubServiceError(Exception):
    """Erreur lors d'un appel cross-service vers Ad HUB.
    Attributs : status_code (int|None), detail (str)."""

    def __init__(self, status_code: Optional[int], detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Ad HUB error {status_code}: {detail}")


def _hub_get(path: str, jwt_token: str) -> dict:
    """GET HTTP vers Ad HUB avec Bearer JWT user. Lève HubServiceError si non-2xx."""
    target_url = f"{HUB_API_URL}{path}"
    try:
        with httpx.Client(timeout=HUB_TIMEOUT_S) as client:
            r = client.get(target_url, headers={"Authorization": f"Bearer {jwt_token}"})
    except httpx.RequestError as e:
        raise HubServiceError(None, f"Network error: {e}") from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text[:200])
        except Exception:
            detail = r.text[:200]
        raise HubServiceError(r.status_code, str(detail))
    return r.json()


def _hub_post(path: str, jwt_token: str, body: dict) -> dict:
    """POST JSON vers Ad HUB avec Bearer JWT user. Lève HubServiceError si non-2xx."""
    target_url = f"{HUB_API_URL}{path}"
    try:
        with httpx.Client(timeout=HUB_TIMEOUT_S) as client:
            r = client.post(target_url, json=body, headers={"Authorization": f"Bearer {jwt_token}"})
    except httpx.RequestError as e:
        raise HubServiceError(None, f"Network error: {e}") from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text[:200])
        except Exception:
            detail = r.text[:200]
        raise HubServiceError(r.status_code, str(detail))
    return r.json() if r.content else {}


def _hub_delete(path: str, jwt_token: str) -> None:
    """DELETE vers Ad HUB (soft-delete document) — best-effort, lève si non-2xx."""
    target_url = f"{HUB_API_URL}{path}"
    try:
        with httpx.Client(timeout=HUB_TIMEOUT_S) as client:
            r = client.delete(target_url, headers={"Authorization": f"Bearer {jwt_token}"})
    except httpx.RequestError as e:
        raise HubServiceError(None, f"Network error: {e}") from e
    if r.status_code >= 400:
        raise HubServiceError(r.status_code, r.text[:200])


def list_org_projects(jwt_token: str, limit: int = 200) -> list:
    """Liste les projets de l'org active de l'utilisateur (GET HUB /api/projects).
    Retourne la liste des projets (dicts : id, name, code, statut, client_nom…)."""
    data = _hub_get(f"/api/projects?limit={int(limit)}", jwt_token)
    if isinstance(data, list):
        return data
    return data.get("projects", []) or []


# ── Dépôt d'un document dans la GED HUB (Chantier 3) ─────────────────────────
def resolve_category_id(jwt_token: str, name: str, phase: str = "pre_construction"):
    """Résout l'id de la catégorie GED par NOM pour l'org (GET HUB
    /api/organization/document-categories). None si introuvable."""
    data = _hub_get(f"/api/organization/document-categories?phase={phase}", jwt_token)
    cats = data if isinstance(data, list) else (data.get("categories", []) or [])
    for c in cats:
        if c.get("name") == name and c.get("active", True):
            return c.get("id")
    return None


def _count_disc_docs(disc: dict) -> int:
    return len(disc.get("documents") or []) + sum(_count_disc_docs(c) for c in (disc.get("children") or []))


def count_category_docs(jwt_token: str, project_id: int, category_name: str, phase: str = "pre_construction") -> int:
    """Compte les documents (status final) d'une catégorie d'un projet — pour le
    versioning. GET HUB /api/projects/{id}/documents (l'arbre ne renvoie que les
    docs 'final'). 0 si la catégorie n'existe pas ou est vide."""
    data = _hub_get(f"/api/projects/{int(project_id)}/documents?phase={phase}", jwt_token)
    for cat in data.get("categories", []) or []:
        if cat.get("name") == category_name:
            n = len(cat.get("documents_uncategorized") or [])
            for d in cat.get("disciplines") or []:
                n += _count_disc_docs(d)
            return n
    return 0


def init_project_document(jwt_token: str, project_id: int, payload: dict) -> dict:
    """Init upload GED-natif (POST HUB /api/projects/{id}/documents). Retourne
    {document_id, upload_url, r2_key}. Lève HubServiceError (403 = write_docs refusé)."""
    return _hub_post(f"/api/projects/{int(project_id)}/documents", jwt_token, payload)


def put_presigned(upload_url: str, data: bytes, content_type: str) -> None:
    """PUT direct des bytes vers l'URL R2 PRÉSIGNÉE (self-authenticated, sans
    Bearer). Lève HubServiceError si non-2xx."""
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.put(upload_url, content=data, headers={"Content-Type": content_type})
    except httpx.RequestError as e:
        raise HubServiceError(None, f"R2 PUT network error: {e}") from e
    if r.status_code >= 400:
        raise HubServiceError(r.status_code, f"R2 PUT échec: {r.text[:200]}")


def confirm_document(jwt_token: str, document_id: str, sha256_hash: str) -> dict:
    """Confirme l'upload (POST HUB /api/documents/{id}/confirm) -> status='final'."""
    return _hub_post(f"/api/documents/{document_id}/confirm", jwt_token, {"sha256_hash": sha256_hash})


def delete_document(jwt_token: str, document_id: str) -> None:
    """Soft-delete d'un document HUB (cleanup-on-failure)."""
    _hub_delete(f"/api/documents/{document_id}", jwt_token)
