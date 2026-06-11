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


def list_org_projects(jwt_token: str, limit: int = 200) -> list:
    """Liste les projets de l'org active de l'utilisateur (GET HUB /api/projects).
    Retourne la liste des projets (dicts : id, name, code, statut, client_nom…)."""
    data = _hub_get(f"/api/projects?limit={int(limit)}", jwt_token)
    if isinstance(data, list):
        return data
    return data.get("projects", []) or []
