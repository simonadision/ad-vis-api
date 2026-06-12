"""
Ad VIS — Notes de calendrier par PROJET.

Notes datées libres rattachées à un PROJET HUB (central_project_id), DISTINCTES
des visites (calendrier mensuel : clic sur une date → une ou plusieurs notes).
CRUD pur sur ad_vis.notes_calendrier.

ISOLATION MULTI-TENANT (stricte) : organization_id n'est JAMAIS lu du client —
toujours forcé depuis le JWT (user["organization_id"]), exactement comme les
visites. Toute lecture/écriture filtre organization_id :
  • create : INSERT avec organization_id = org du JWT (le projet vient de l'URL) ;
  • list   : WHERE organization_id = org ET central_project_id = projet ;
  • patch/delete : WHERE id = %s AND organization_id = org → une note d'un autre
    org = 0 ligne = 404.
Soft-delete réversible (deleted_at), jamais de hard-delete. Les listes ne
renvoient que les notes non supprimées.
"""
import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from psycopg.rows import dict_row


def _iso(v):
    return v.isoformat() if v else None


def _serialize(r):
    return {
        "id": r["id"],
        "central_project_id": r["central_project_id"],
        "date_note": r["date_note"].isoformat() if r.get("date_note") else None,
        "texte": r["texte"],
        "auteur_user_id": r.get("auteur_user_id"),
        "auteur_nom": r.get("auteur_nom"),
        "created_at": _iso(r.get("created_at")),
        "updated_at": _iso(r.get("updated_at")),
    }


def register_calendar_routes(get_conn, jwt_user):
    router = APIRouter()

    # ── POST création d'une note datée (rattachée au projet) ───────────────
    @router.post("/api/projects/{project_id}/notes-calendrier", status_code=201)
    def create_cal_note(project_id: int, data: dict = Body(...), user=Depends(jwt_user)):
        date_str = (data.get("date_note") or "").strip()
        if not date_str:
            raise HTTPException(status_code=400, detail="date_note requise (YYYY-MM-DD)")
        try:
            datetime.date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="date_note invalide (attendu YYYY-MM-DD)")
        texte = (data.get("texte") or "").strip()
        if not texte:
            raise HTTPException(status_code=400, detail="texte requis (non vide)")
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                cur.execute(
                    """
                    INSERT INTO ad_vis.notes_calendrier
                      (central_project_id, organization_id, date_note, texte, auteur_user_id)
                    VALUES (%s, %s, %s::date, %s, %s)
                    RETURNING id, central_project_id, date_note, texte, auteur_user_id,
                              created_at, updated_at
                    """,
                    (project_id, user["organization_id"], date_str, texte, user["id"]),
                )
                row = cur.fetchone()
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()
        return {"note": _serialize(row)}

    # ── GET liste des notes d'un projet (org-scopée, filtre mois optionnel) ─
    @router.get("/api/projects/{project_id}/notes-calendrier")
    def list_cal_notes(
        project_id: int,
        month: str = Query(None, description="Filtre optionnel AAAA-MM"),
        user=Depends(jwt_user),
    ):
        where = ["nc.organization_id = %s", "nc.central_project_id = %s", "nc.deleted_at IS NULL"]
        params = [user["organization_id"], project_id]
        if month:
            try:
                first = datetime.date.fromisoformat(month + "-01")
            except ValueError:
                raise HTTPException(status_code=400, detail="month invalide (attendu AAAA-MM)")
            where.append(
                "nc.date_note >= %s::date AND nc.date_note < (%s::date + INTERVAL '1 month')"
            )
            params.extend([first.isoformat(), first.isoformat()])
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                cur.execute(
                    f"""
                    SELECT nc.id, nc.central_project_id, nc.date_note, nc.texte,
                           nc.auteur_user_id, u.nom AS auteur_nom,
                           nc.created_at, nc.updated_at
                    FROM ad_vis.notes_calendrier nc
                    LEFT JOIN ad_vis.users u ON u.id = nc.auteur_user_id
                    WHERE {' AND '.join(where)}
                    ORDER BY nc.date_note ASC, nc.id ASC
                    """,
                    params,
                )
                rows = cur.fetchall()
            finally:
                cur.close()
        finally:
            conn.close()
        return {"notes": [_serialize(r) for r in rows], "total": len(rows)}

    # ── PATCH texte d'une note ─────────────────────────────────────────────
    @router.patch("/api/notes-calendrier/{note_id}")
    def patch_cal_note(note_id: int, data: dict = Body(...), user=Depends(jwt_user)):
        if "texte" not in data:
            raise HTTPException(status_code=400, detail="Aucun champ modifiable (texte)")
        texte = (data.get("texte") or "").strip()
        if not texte:
            raise HTTPException(status_code=400, detail="texte ne peut pas être vide")
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                cur.execute(
                    """
                    UPDATE ad_vis.notes_calendrier
                    SET texte = %s, updated_at = NOW()
                    WHERE id = %s AND organization_id = %s AND deleted_at IS NULL
                    RETURNING id, central_project_id, date_note, texte, auteur_user_id,
                              created_at, updated_at
                    """,
                    (texte, note_id, user["organization_id"]),
                )
                row = cur.fetchone()
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Note introuvable")
        return {"note": _serialize(row)}

    # ── DELETE (soft-delete réversible) ────────────────────────────────────
    @router.delete("/api/notes-calendrier/{note_id}")
    def delete_cal_note(note_id: int, user=Depends(jwt_user)):
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                cur.execute(
                    """
                    UPDATE ad_vis.notes_calendrier
                    SET deleted_at = NOW()
                    WHERE id = %s AND organization_id = %s AND deleted_at IS NULL
                    RETURNING id
                    """,
                    (note_id, user["organization_id"]),
                )
                row = cur.fetchone()
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Note introuvable")
        return {"deleted": True, "id": note_id}

    return router
