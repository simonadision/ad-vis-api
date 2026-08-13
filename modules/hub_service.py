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


def _hub_patch(path: str, jwt_token: str, body: dict) -> dict:
    """PATCH JSON vers Ad HUB avec Bearer JWT user. Lève HubServiceError si non-2xx.

    SANS APPELANT depuis le 2026-08-14 : son seul usage était de basculer
    `modules_actifs.ad_vis` (voir la note historique plus bas). Conservé comme
    primitive, mais réfléchir à deux fois avant de la rebrancher — Ad VIS n'a
    plus aucune raison d'écrire dans la fiche projet du hub.
    """
    target_url = f"{HUB_API_URL}{path}"
    try:
        with httpx.Client(timeout=HUB_TIMEOUT_S) as client:
            r = client.patch(target_url, json=body,
                             headers={"Authorization": f"Bearer {jwt_token}"})
    except httpx.RequestError as e:
        raise HubServiceError(None, f"Network error: {e}") from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text[:200])
        except Exception:
            detail = r.text[:200]
        raise HubServiceError(r.status_code, str(detail))
    return r.json() if r.content else {}


def list_org_projects(jwt_token: str, limit: int = 200) -> list:
    """Catalogue BRUT des projets de l'org (GET HUB /api/projects).

    Sert UNIQUEMENT à alimenter la modale « Nouveau projet » : c'est le
    catalogue dans lequel on vient piocher. L'écran de sélection d'Ad VIS, lui,
    lit ad_vis.vis_projects (opt-in) — voir migration 012.

    Plus aucun filtre ici. Le filtrage opt-OUT (modules_actifs.ad_vis) posé le
    2026-08-13 est remplacé par l'opt-IN de la table de liaison : retirer les
    projets un par un était l'inverse du besoin de Simon.

    Retourne la liste des projets (dicts : id, name, code, statut, client_nom…).
    """
    data = _hub_get(f"/api/projects?limit={int(limit)}", jwt_token)
    if isinstance(data, list):
        return data
    return data.get("projects", []) or []


def fetch_hub_project(jwt_token: str, project_id) -> dict:
    """Fiche d'UN projet hub, pour alimenter le cache d'affichage à la liaison.

    Déballe {"project": {...}} quand le hub enveloppe sa réponse. Lève
    HubServiceError : l'appelant décide si l'absence de libellé est bloquante
    (elle ne l'est pas — le cache est un confort, pas une condition).
    """
    raw = _hub_get(f"/api/projects/{int(project_id)}", jwt_token)
    if isinstance(raw, dict) and isinstance(raw.get("project"), dict):
        return raw["project"]
    return raw if isinstance(raw, dict) else {}


def fetch_libelles_arborescence(jwt_token: str) -> dict:
    """Correspondance segment de code -> nom complet, + sections de l'arbre.

    Alimente la modale « Nouveau projet », qui reconstruit l'arborescence a
    partir des codes projet (le code EST le chemin). Confort d'affichage
    uniquement : sans ces libelles la modale montre les segments bruts
    (CED, AOPU) au lieu des noms complets. NE RAISE JAMAIS — une modale qui
    refuse de s'ouvrir parce qu'un libelle manque rendrait la liaison
    impossible, donc l'opt-in inapplicable.
    """
    vide = {"libelles": {}, "sections": []}
    if not jwt_token:
        return vide
    try:
        data = _hub_get("/api/codification/libelles", jwt_token)
    except Exception:
        return vide
    if not isinstance(data, dict):
        return vide
    return {
        "libelles": data.get("libelles") or {},
        "sections": data.get("sections") or [],
    }


def hub_project_belongs_to_org(jwt_token: str, project_id) -> bool:
    """Vérifie que le projet appartient à l'org du user courant (via le hub).

    True s'il figure dans le catalogue retourné par list_org_projects, False
    sinon. Appelé AVANT toute insertion dans ad_vis.vis_projects : Ad VIS ne
    décide pas des droits, il les fait trancher par le hub.
    """
    try:
        return any(
            int(p.get("id")) == int(project_id)
            for p in list_org_projects(jwt_token)
            if p.get("id") is not None
        )
    except Exception:
        return False


# NOTE HISTORIQUE — `retirer_projet_du_module` a vécu ici du 2026-08-13 au
# 2026-08-14. Elle basculait `modules_actifs.ad_vis` à false côté hub (avec
# relecture-fusion des autres drapeaux, sans quoi le validateur du hub aurait
# désactivé Ad BUD et Ad EST du même coup), parce qu'Ad VIS n'avait alors
# aucune table de projets et que le seul moyen de masquer un projet était de
# poser un drapeau sur le hub.
#
# Avec ad_vis.vis_projects (migration 012), l'appartenance est LOCALE : délier
# = soft-delete de la ligne, dans vis_api. Plus besoin d'écrire dans le hub
# pour gérer l'écran d'Ad VIS — un module qui n'écrit pas chez le voisin ne
# peut pas casser le voisin.
#
# Ce qu'elle avait corrigé reste vrai et ne doit pas être défait : avant elle,
# ce chemin appelait DELETE /api/projects/{id}, le soft-delete du hub. C'est
# ce piège qui a détruit six projets de production via Ad VIS.


def create_project(jwt_token: str, body: dict) -> dict:
    """Crée un projet dans Ad HUB (POST HUB /api/projects) — création MINIMALE
    depuis Ad VIS (nom + type_mandat ; le code est auto-généré par le gabarit de
    l'org côté HUB). Retourne la row projet (déballe {"project": {...}}). Lève
    HubServiceError si non-2xx (le caller renvoie un message clair, pas de blocage)."""
    raw = _hub_post("/api/projects", jwt_token, body)
    proj = raw.get("project") if isinstance(raw, dict) and isinstance(raw.get("project"), dict) else raw
    if not proj or "id" not in proj:
        raise HubServiceError(502, f"Réponse Ad HUB sans project.id : {str(raw)[:200]}")
    return proj


def get_codification(jwt_token: str) -> dict:
    """Gabarit de codification de l'org + APERÇU du prochain code projet
    (GET HUB /api/organization/codification). Retourne {codification, preview}."""
    return _hub_get("/api/organization/codification", jwt_token)


# ── Employés + postes de l'org (Brique B — modale création) ─────────────────
def list_org_users(jwt_token: str) -> list:
    """Users de l'org (proxy GET HUB /api/organization/users, JWT forwardé →
    le HUB re-valide l'org → pas de fuite cross-org). Lecture ouverte à tout
    user de l'org. Renvoie [{id, nom, email, fonction_nom, status, …}]."""
    data = _hub_get("/api/organization/users", jwt_token)
    return data.get("users", []) or []


def list_fonctions(jwt_token: str) -> list:
    """Référentiel de fonctions/postes de l'org (proxy GET HUB
    /api/organization/fonctions). Renvoie [{id, nom, ordre, active}]."""
    data = _hub_get("/api/organization/fonctions", jwt_token)
    return data.get("fonctions", []) or []


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
