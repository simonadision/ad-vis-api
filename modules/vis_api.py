"""
Ad VIS — routes métier (Sprint 0) : proxy projets HUB + CRUD visites.

Sprint 0 = fondations : sélection d'un projet HUB, choix du profil
(estimateur / contremaître), création d'une visite (brouillon, date du jour),
liste et reprise des visites d'un projet. Observations / photos / checklist =
Sprints 1-2 (tables déjà créées en migration, endpoints à venir).

Sécurité multi-tenant : organization_id est TOUJOURS pris du JWT (jamais du
payload). Les lectures/écritures sont scopées organization_id.
"""
import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from psycopg.rows import dict_row

from modules import hub_service

PROFILS = ("estimateur", "contremaitre")
STATUTS = ("brouillon", "finalise")


def _serialize_visite(r: dict) -> dict:
    return {
        "id": r["id"],
        "central_project_id": r["central_project_id"],
        "organization_id": str(r["organization_id"]),
        "profil": r["profil"],
        "auteur_user_id": r["auteur_user_id"],
        "auteur_nom": r.get("auteur_nom"),
        "date_visite": r["date_visite"].isoformat() if r.get("date_visite") else None,
        "statut": r["statut"],
        "meteo": r.get("meteo"),
        "nom_responsable": r.get("nom_responsable"),
        "poste_responsable": r.get("poste_responsable"),
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
    }


def register_vis_routes(get_conn, jwt_user):
    router = APIRouter()

    # ── Proxy : liste des projets HUB de l'org (pour l'écran de sélection) ──
    @router.get("/v2/ad-hub-projects")
    def ad_hub_projects(user=Depends(jwt_user)):
        """Liste les projets de l'org (proxy GET HUB /api/projects, JWT user
        forwardé → le HUB valide l'organization_id)."""
        try:
            projets = hub_service.list_org_projects(user["_jwt"])
        except hub_service.HubServiceError as e:
            raise HTTPException(status_code=e.status_code or 502, detail=f"Ad HUB : {e.detail}")
        # On ne renvoie que ce dont l'écran de sélection a besoin.
        return {
            "projects": [
                {
                    "id": p.get("id"),
                    "name": p.get("name"),
                    "code": p.get("code"),
                    "statut": p.get("statut"),
                    "client_nom": p.get("client_nom"),
                }
                for p in projets
            ]
        }

    # ── POST création d'une visite (brouillon) ─────────────────────────────
    @router.post("/api/visites", status_code=201)
    def create_visite(data: dict = Body(...), user=Depends(jwt_user)):
        central_project_id = data.get("central_project_id")
        if central_project_id in (None, ""):
            raise HTTPException(status_code=400, detail="central_project_id requis")
        try:
            central_project_id = int(central_project_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="central_project_id invalide")

        profil = data.get("profil")
        if profil not in PROFILS:
            raise HTTPException(status_code=400, detail=f"profil invalide (valeurs : {', '.join(PROFILS)})")

        date_visite = data.get("date_visite")  # 'YYYY-MM-DD' optionnel
        if date_visite:
            try:
                datetime.date.fromisoformat(date_visite)
            except ValueError:
                raise HTTPException(status_code=400, detail="date_visite invalide (attendu YYYY-MM-DD)")
        meteo = (data.get("meteo") or None)
        nom_resp = (data.get("nom_responsable") or "").strip() or None
        poste_resp = (data.get("poste_responsable") or "").strip() or None

        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                cur.execute(
                    """
                    INSERT INTO ad_vis.visites
                      (central_project_id, organization_id, profil, auteur_user_id,
                       date_visite, statut, meteo, nom_responsable, poste_responsable)
                    VALUES (%s, %s, %s, %s, COALESCE(%s::date, CURRENT_DATE), 'brouillon', %s, %s, %s)
                    RETURNING id, central_project_id, organization_id, profil, auteur_user_id,
                              date_visite, statut, meteo, nom_responsable, poste_responsable, created_at
                    """,
                    (central_project_id, user["organization_id"], profil, user["id"],
                     date_visite, meteo, nom_resp, poste_resp),
                )
                row = cur.fetchone()
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()
        return {"visite": _serialize_visite(row)}

    # ── GET liste des visites d'un projet (org-scopé) ──────────────────────
    @router.get("/api/visites")
    def list_visites(
        project_id: int = Query(..., description="central_project_id du projet HUB"),
        user=Depends(jwt_user),
    ):
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                cur.execute(
                    """
                    SELECT v.id, v.central_project_id, v.organization_id, v.profil,
                           v.auteur_user_id, u.nom AS auteur_nom, v.date_visite,
                           v.statut, v.meteo, v.nom_responsable, v.poste_responsable, v.created_at
                    FROM ad_vis.visites v
                    LEFT JOIN ad_vis.users u ON u.id = v.auteur_user_id
                    WHERE v.organization_id = %s AND v.central_project_id = %s
                    ORDER BY v.date_visite DESC, v.id DESC
                    """,
                    (user["organization_id"], project_id),
                )
                rows = cur.fetchall()
            finally:
                cur.close()
        finally:
            conn.close()
        return {"visites": [_serialize_visite(r) for r in rows], "total": len(rows)}

    # ── GET détail d'une visite (reprendre un brouillon) ───────────────────
    @router.get("/api/visites/{visite_id}")
    def get_visite(visite_id: int, user=Depends(jwt_user)):
        row = _fetch_visite_scoped(get_conn, visite_id, user["organization_id"])
        if not row:
            raise HTTPException(status_code=404, detail="Visite introuvable")
        return {"visite": _serialize_visite(row)}

    # ── PATCH visite (statut / météo) ──────────────────────────────────────
    @router.patch("/api/visites/{visite_id}")
    def update_visite(visite_id: int, data: dict = Body(...), user=Depends(jwt_user)):
        sets, params = [], []
        if "statut" in data:
            if data["statut"] not in STATUTS:
                raise HTTPException(status_code=400, detail="statut invalide")
            sets.append("statut = %s"); params.append(data["statut"])
        if "meteo" in data:
            sets.append("meteo = %s"); params.append(data.get("meteo") or None)
        if "date_visite" in data and data["date_visite"]:
            try:
                datetime.date.fromisoformat(data["date_visite"])
            except ValueError:
                raise HTTPException(status_code=400, detail="date_visite invalide (YYYY-MM-DD)")
            sets.append("date_visite = %s"); params.append(data["date_visite"])
        if "nom_responsable" in data:
            sets.append("nom_responsable = %s"); params.append((data.get("nom_responsable") or "").strip() or None)
        if "poste_responsable" in data:
            sets.append("poste_responsable = %s"); params.append((data.get("poste_responsable") or "").strip() or None)
        if not sets:
            raise HTTPException(status_code=400, detail="Aucun champ modifiable (statut/meteo/date_visite/nom_responsable/poste_responsable)")

        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                cur.execute(
                    f"""
                    UPDATE ad_vis.visites SET {', '.join(sets)}
                    WHERE id = %s AND organization_id = %s
                    RETURNING id, central_project_id, organization_id, profil, auteur_user_id,
                              date_visite, statut, meteo, nom_responsable, poste_responsable, created_at
                    """,
                    params + [visite_id, user["organization_id"]],
                )
                row = cur.fetchone()
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Visite introuvable")
        return {"visite": _serialize_visite(row)}

    return router


def _fetch_visite_scoped(get_conn, visite_id: int, org_id):
    conn = get_conn()
    try:
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute(
                """
                SELECT v.id, v.central_project_id, v.organization_id, v.profil,
                       v.auteur_user_id, u.nom AS auteur_nom, v.date_visite,
                       v.statut, v.meteo, v.nom_responsable, v.poste_responsable, v.created_at
                FROM ad_vis.visites v
                LEFT JOIN ad_vis.users u ON u.id = v.auteur_user_id
                WHERE v.id = %s AND v.organization_id = %s
                """,
                (visite_id, org_id),
            )
            return cur.fetchone()
        finally:
            cur.close()
    finally:
        conn.close()
