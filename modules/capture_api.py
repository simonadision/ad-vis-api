"""
Ad VIS — Sprint 1 Brique 1 : capture « dans la visite » — notes texte +
observations typées. CRUD pur sur ad_vis.notes / ad_vis.observations.

ISOLATION MULTI-TENANT (stricte) : une note/observation appartient à une
`visite` ; l'organization_id n'est JAMAIS lu du client. Toute opération est
cloisonnée par l'org de la VISITE PARENTE (issue du JWT) :
  • create/list : on vérifie d'abord que la visite appartient à l'org (404 sinon) ;
  • patch/delete : le WHERE joint ad_vis.visites et filtre v.organization_id —
    une note d'un autre org → 0 ligne → 404.
Soft-delete réversible (deleted_at), jamais de hard-delete. Les listes ne
renvoient que les éléments non supprimés.
"""
from fastapi import APIRouter, Body, Depends, HTTPException
from psycopg.rows import dict_row

OBS_TYPES = ("acces", "contrainte", "etat_existant", "question", "constat")


def _iso(v):
    return v.isoformat() if v else None


def _serialize_note(r):
    return {
        "id": r["id"], "visite_id": r["visite_id"], "texte": r["texte"],
        "ordre": r["ordre"], "created_at": _iso(r.get("created_at")),
        "updated_at": _iso(r.get("updated_at")),
    }


def _serialize_obs(r):
    return {
        "id": r["id"], "visite_id": r["visite_id"], "type": r["type"],
        "texte": r["texte"], "ordre": r["ordre"],
        "created_at": _iso(r.get("created_at")), "updated_at": _iso(r.get("updated_at")),
    }


def _assert_visite_org(cur, visite_id, org_id):
    """404 si la visite n'existe pas OU n'appartient pas à l'org du JWT."""
    cur.execute(
        "SELECT id FROM ad_vis.visites WHERE id = %s AND organization_id = %s",
        (visite_id, org_id),
    )
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Visite introuvable")


def register_capture_routes(get_conn, jwt_user):
    router = APIRouter()

    # ════════════════════════ NOTES ════════════════════════
    @router.post("/api/visites/{visite_id}/notes", status_code=201)
    def create_note(visite_id: int, data: dict = Body(...), user=Depends(jwt_user)):
        texte = (data.get("texte") or "").strip()
        if not texte:
            raise HTTPException(status_code=400, detail="texte requis (non vide)")
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                _assert_visite_org(cur, visite_id, user["organization_id"])
                cur.execute(
                    """
                    INSERT INTO ad_vis.notes (visite_id, texte, ordre)
                    VALUES (%s, %s, COALESCE(
                      (SELECT MAX(ordre) + 1 FROM ad_vis.notes
                       WHERE visite_id = %s AND deleted_at IS NULL), 0))
                    RETURNING id, visite_id, texte, ordre, created_at, updated_at
                    """,
                    (visite_id, texte, visite_id),
                )
                row = cur.fetchone()
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()
        return {"note": _serialize_note(row)}

    @router.get("/api/visites/{visite_id}/notes")
    def list_notes(visite_id: int, user=Depends(jwt_user)):
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                _assert_visite_org(cur, visite_id, user["organization_id"])
                cur.execute(
                    """
                    SELECT id, visite_id, texte, ordre, created_at, updated_at
                    FROM ad_vis.notes
                    WHERE visite_id = %s AND deleted_at IS NULL
                    ORDER BY ordre ASC, id ASC
                    """,
                    (visite_id,),
                )
                rows = cur.fetchall()
            finally:
                cur.close()
        finally:
            conn.close()
        return {"notes": [_serialize_note(r) for r in rows], "total": len(rows)}

    @router.patch("/api/notes/{note_id}")
    def patch_note(note_id: int, data: dict = Body(...), user=Depends(jwt_user)):
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
                    UPDATE ad_vis.notes n SET texte = %s, updated_at = NOW()
                    FROM ad_vis.visites v
                    WHERE n.id = %s AND n.visite_id = v.id
                      AND v.organization_id = %s AND n.deleted_at IS NULL
                    RETURNING n.id, n.visite_id, n.texte, n.ordre, n.created_at, n.updated_at
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
        return {"note": _serialize_note(row)}

    @router.delete("/api/notes/{note_id}")
    def delete_note(note_id: int, user=Depends(jwt_user)):
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                cur.execute(
                    """
                    UPDATE ad_vis.notes n SET deleted_at = NOW()
                    FROM ad_vis.visites v
                    WHERE n.id = %s AND n.visite_id = v.id
                      AND v.organization_id = %s AND n.deleted_at IS NULL
                    RETURNING n.id
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

    # ════════════════════ OBSERVATIONS ════════════════════
    @router.post("/api/visites/{visite_id}/observations", status_code=201)
    def create_obs(visite_id: int, data: dict = Body(...), user=Depends(jwt_user)):
        type_ = data.get("type")
        if type_ not in OBS_TYPES:
            raise HTTPException(status_code=400, detail=f"type invalide (valeurs : {', '.join(OBS_TYPES)})")
        texte = (data.get("texte") or "").strip()
        if not texte:
            raise HTTPException(status_code=400, detail="texte requis (non vide)")
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                _assert_visite_org(cur, visite_id, user["organization_id"])
                cur.execute(
                    """
                    INSERT INTO ad_vis.observations (visite_id, type, texte, ordre)
                    VALUES (%s, %s, %s, COALESCE(
                      (SELECT MAX(ordre) + 1 FROM ad_vis.observations
                       WHERE visite_id = %s AND deleted_at IS NULL), 0))
                    RETURNING id, visite_id, type, texte, ordre, created_at, updated_at
                    """,
                    (visite_id, type_, texte, visite_id),
                )
                row = cur.fetchone()
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()
        return {"observation": _serialize_obs(row)}

    @router.get("/api/visites/{visite_id}/observations")
    def list_obs(visite_id: int, user=Depends(jwt_user)):
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                _assert_visite_org(cur, visite_id, user["organization_id"])
                cur.execute(
                    """
                    SELECT id, visite_id, type, texte, ordre, created_at, updated_at
                    FROM ad_vis.observations
                    WHERE visite_id = %s AND deleted_at IS NULL
                    ORDER BY ordre ASC, id ASC
                    """,
                    (visite_id,),
                )
                rows = cur.fetchall()
            finally:
                cur.close()
        finally:
            conn.close()
        return {"observations": [_serialize_obs(r) for r in rows], "total": len(rows)}

    @router.patch("/api/observations/{obs_id}")
    def patch_obs(obs_id: int, data: dict = Body(...), user=Depends(jwt_user)):
        sets, params = [], []
        if "type" in data:
            if data["type"] not in OBS_TYPES:
                raise HTTPException(status_code=400, detail="type invalide")
            sets.append("type = %s"); params.append(data["type"])
        if "texte" in data:
            texte = (data.get("texte") or "").strip()
            if not texte:
                raise HTTPException(status_code=400, detail="texte ne peut pas être vide")
            sets.append("texte = %s"); params.append(texte)
        if not sets:
            raise HTTPException(status_code=400, detail="Aucun champ modifiable (type/texte)")
        sets.append("updated_at = NOW()")
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                cur.execute(
                    f"""
                    UPDATE ad_vis.observations o SET {', '.join(sets)}
                    FROM ad_vis.visites v
                    WHERE o.id = %s AND o.visite_id = v.id
                      AND v.organization_id = %s AND o.deleted_at IS NULL
                    RETURNING o.id, o.visite_id, o.type, o.texte, o.ordre, o.created_at, o.updated_at
                    """,
                    params + [obs_id, user["organization_id"]],
                )
                row = cur.fetchone()
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Observation introuvable")
        return {"observation": _serialize_obs(row)}

    @router.delete("/api/observations/{obs_id}")
    def delete_obs(obs_id: int, user=Depends(jwt_user)):
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                cur.execute(
                    """
                    UPDATE ad_vis.observations o SET deleted_at = NOW()
                    FROM ad_vis.visites v
                    WHERE o.id = %s AND o.visite_id = v.id
                      AND v.organization_id = %s AND o.deleted_at IS NULL
                    RETURNING o.id
                    """,
                    (obs_id, user["organization_id"]),
                )
                row = cur.fetchone()
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Observation introuvable")
        return {"deleted": True, "id": obs_id}

    return router
