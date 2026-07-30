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

    # ── Proxy : suppression d'un projet HUB depuis la carte de sélection ──────
    @router.delete("/v2/ad-hub-projects/{project_id}", status_code=200)
    def delete_ad_hub_project(project_id: int, user=Depends(jwt_user)):
        """Supprime un projet (proxy DELETE HUB /api/projects/{id}).

        Ad VIS n'a pas de table projets : il n'y a donc rien à détacher côté
        Ad VIS, la suppression porte sur la fiche Ad HUB elle-même. Le HUB
        applique son soft-delete et vérifie les droits à partir du JWT
        forwardé — aucune décision d'autorisation n'est prise ici.
        """
        try:
            hub_service.delete_project(user["_jwt"], project_id)
        except hub_service.HubServiceError as e:
            raise HTTPException(status_code=e.status_code or 502, detail=f"Ad HUB : {e.detail}")
        return {"deleted": True, "id": project_id}

    # ── Aperçu du code projet (gabarit de l'org) — modale « + Nouveau projet » ──
    @router.get("/v2/codification-preview")
    def codification_preview(user=Depends(jwt_user)):
        """Aperçu illustratif du prochain code projet (gabarit de l'org, proxy HUB)."""
        try:
            data = hub_service.get_codification(user["_jwt"])
        except hub_service.HubServiceError as e:
            raise HTTPException(status_code=e.status_code or 502, detail=f"Ad HUB : {e.detail}")
        return {"preview": data.get("preview"), "codification": data.get("codification")}

    # ── POST création MINIMALE d'un projet (nom + type_mandat) via le HUB ──────
    # Ad VIS n'a PAS de table projets : les visites référencent central_project_id
    # (= id hub). On crée donc la fiche dans Ad HUB et on renvoie son id + code
    # (auto-généré par le gabarit de l'org) pour démarrer la visite immédiatement.
    _VIS_TYPE_MANDATS = ("soumission", "budget", "services")

    @router.post("/vis/projets/create-via-hub", status_code=201)
    def create_projet_via_hub(data: dict = Body(...), user=Depends(jwt_user)):
        name = (data.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="Nom du projet requis")
        type_mandat = (data.get("type_mandat") or "").strip() or None
        if type_mandat and type_mandat not in _VIS_TYPE_MANDATS:
            raise HTTPException(status_code=422,
                                detail=f"type_mandat invalide (valeurs : {', '.join(_VIS_TYPE_MANDATS)})")
        body = {"name": name, "type_mandat": type_mandat, "source": "ad_vis"}
        # Synchrone : l'utilisateur attend de pouvoir démarrer sa visite. Hub down /
        # rejet -> 502 avec message clair (le front réessaie, l'app n'est pas bloquée).
        try:
            proj = hub_service.create_project(user["_jwt"], body)
        except hub_service.HubServiceError as e:
            raise HTTPException(status_code=502, detail=f"Échec création projet dans Ad HUB : {e.detail}")
        return {"project": {
            "id": proj.get("id"), "name": proj.get("name"),
            "code": proj.get("code"), "statut": proj.get("statut"),
            "client_nom": proj.get("client_nom"),
        }}

    # ── Proxy : employés + postes de l'org (modale création, Brique B) ─────
    @router.get("/v2/org-users")
    def org_users(user=Depends(jwt_user)):
        """Users de l'org (proxy HUB). Org-scopé par le JWT (le HUB filtre)."""
        try:
            users = hub_service.list_org_users(user["_jwt"])
        except hub_service.HubServiceError as e:
            raise HTTPException(status_code=e.status_code or 502, detail=f"Ad HUB : {e.detail}")
        return {"users": [
            {"id": u.get("id"), "nom": u.get("nom"), "email": u.get("email"),
             "fonction_nom": u.get("fonction_nom"), "status": u.get("status")}
            for u in users
        ]}

    @router.get("/v2/org-fonctions")
    def org_fonctions(user=Depends(jwt_user)):
        """Référentiel de postes/fonctions de l'org (proxy HUB) — suggestions."""
        try:
            fonctions = hub_service.list_fonctions(user["_jwt"])
        except hub_service.HubServiceError as e:
            raise HTTPException(status_code=e.status_code or 502, detail=f"Ad HUB : {e.detail}")
        return {"fonctions": [{"id": f.get("id"), "nom": f.get("nom")} for f in fonctions]}

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

        # profil NEUTRALISÉ (checklist commune) : défaut serveur si la modale ne
        # l'envoie plus. Reste NOT NULL en base ; ne filtre plus la checklist.
        profil = data.get("profil") or "estimateur"
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
                      AND v.deleted_at IS NULL
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

    # ── DELETE visite = SOFT-delete réversible + cascade soft-delete contenus ─
    @router.delete("/api/visites/{visite_id}")
    def delete_visite(visite_id: int, user=Depends(jwt_user)):
        """Soft-delete (deleted_at) — JAMAIS de hard-delete. Org-scopé via la
        visite (404 cross-org). Cascade soft-delete des contenus liés (notes,
        observations, photos, réponses checklist) DANS LA MÊME TRANSACTION : NOW()
        y est constant → même deleted_at pour la visite et ses contenus (permet une
        restauration précise par super_admin). NE TOUCHE PAS les objets R2 des
        photos (conservés) ni le HUB/GED (enregistrement officiel indépendant)."""
        org = user["organization_id"]
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                # 1) la visite (idempotent : seulement si pas déjà supprimée)
                cur.execute(
                    """
                    UPDATE ad_vis.visites SET deleted_at = NOW()
                    WHERE id = %s AND organization_id = %s AND deleted_at IS NULL
                    RETURNING id
                    """,
                    (visite_id, org),
                )
                if cur.fetchone() is None:
                    raise HTTPException(status_code=404, detail="Visite introuvable")
                # 2) cascade sur les contenus (R2 et HUB/GED NON touchés)
                cascade = {}
                for table in ("notes", "observations", "photos", "checklist_reponses"):
                    cur.execute(
                        f"UPDATE ad_vis.{table} SET deleted_at = NOW() "
                        f"WHERE visite_id = %s AND deleted_at IS NULL",
                        (visite_id,),
                    )
                    cascade[table] = cur.rowcount
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()
        return {"deleted": True, "id": visite_id, "cascade": cascade}

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
                WHERE v.id = %s AND v.organization_id = %s AND v.deleted_at IS NULL
                """,
                (visite_id, org_id),
            )
            return cur.fetchone()
        finally:
            cur.close()
    finally:
        conn.close()
