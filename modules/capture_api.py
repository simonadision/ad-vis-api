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

OBS_TYPES = ("acces", "contrainte", "etat_existant", "question", "constat", "manutention")


def _iso(v):
    return v.isoformat() if v else None


def _parse_manut(data):
    """Champs structurés « manutention » (optionnels ; pertinents pour type=
    'manutention'). operation/trajet = texte libre. temps_estime_min = entier
    minutes — NOTE DE RÉFÉRENCE (Ad FLO ne calcule rien). 400 si non entier."""
    operation = (data.get("operation") or "").strip() or None
    trajet = (data.get("trajet") or "").strip() or None
    t = data.get("temps_estime_min")
    if t in (None, ""):
        temps = None
    else:
        try:
            temps = int(t)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="temps_estime_min doit être un entier (minutes)")
    return operation, trajet, temps


def _serialize_note(r):
    return {
        "id": r["id"], "visite_id": r["visite_id"], "texte": r["texte"],
        "ordre": r["ordre"], "created_at": _iso(r.get("created_at")),
        "updated_at": _iso(r.get("updated_at")),
        # Migration 011 — validation. On expose l'horodatage ET un booléen
        # dérivé : le front n'a pas à connaître la convention NULL/non-NULL.
        "valide_at": _iso(r.get("valide_at")),
        "valide": r.get("valide_at") is not None,
    }


def _serialize_obs(r):
    return {
        "id": r["id"], "visite_id": r["visite_id"], "type": r["type"],
        "texte": r["texte"], "ordre": r["ordre"],
        # Champs « manutention » (NULL pour les autres types).
        "operation": r.get("operation"), "trajet": r.get("trajet"),
        "temps_estime_min": r.get("temps_estime_min"),
        "created_at": _iso(r.get("created_at")), "updated_at": _iso(r.get("updated_at")),
        # Migration 011 — validation (cf. _serialize_note).
        "valide_at": _iso(r.get("valide_at")),
        "valide": r.get("valide_at") is not None,
    }


def _assert_visite_org(cur, visite_id, org_id):
    """404 si la visite n'existe pas OU n'appartient pas à l'org du JWT."""
    cur.execute(
        "SELECT id FROM ad_vis.visites WHERE id = %s AND organization_id = %s AND deleted_at IS NULL",
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
                    SELECT id, visite_id, texte, ordre, created_at, updated_at, valide_at
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
        # Migration 011 — deux champs modifiables INDÉPENDANTS : le texte et le
        # statut de validation. Valider ne doit PAS obliger à renvoyer le texte
        # (et inversement), sinon le front risquerait d'écraser l'un avec une
        # valeur périmée. On construit donc le SET dynamiquement.
        has_texte = "texte" in data
        has_valide = "valide" in data
        if not has_texte and not has_valide:
            raise HTTPException(status_code=400, detail="Aucun champ modifiable (texte, valide)")

        sets, params = [], []
        if has_texte:
            texte = (data.get("texte") or "").strip()
            if not texte:
                raise HTTPException(status_code=400, detail="texte ne peut pas être vide")
            sets.append("texte = %s")
            params.append(texte)
            # Une note RÉÉDITÉE redevient à valider : son contenu a changé
            # depuis la relecture, la validation précédente ne vaut plus.
            if not has_valide:
                sets.append("valide_at = NULL")
        if has_valide:
            sets.append("valide_at = " + ("NOW()" if data.get("valide") else "NULL"))
        sets.append("updated_at = NOW()")

        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                cur.execute(
                    f"""
                    UPDATE ad_vis.notes n SET {", ".join(sets)}
                    FROM ad_vis.visites v
                    WHERE n.id = %s AND n.visite_id = v.id
                      AND v.organization_id = %s AND n.deleted_at IS NULL
                    RETURNING n.id, n.visite_id, n.texte, n.ordre, n.created_at,
                              n.updated_at, n.valide_at
                    """,
                    (*params, note_id, user["organization_id"]),
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
        operation, trajet, temps = _parse_manut(data)
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                _assert_visite_org(cur, visite_id, user["organization_id"])
                cur.execute(
                    """
                    INSERT INTO ad_vis.observations
                      (visite_id, type, texte, operation, trajet, temps_estime_min, ordre)
                    VALUES (%s, %s, %s, %s, %s, %s, COALESCE(
                      (SELECT MAX(ordre) + 1 FROM ad_vis.observations
                       WHERE visite_id = %s AND deleted_at IS NULL), 0))
                    RETURNING id, visite_id, type, texte, operation, trajet, temps_estime_min,
                              ordre, created_at, updated_at
                    """,
                    (visite_id, type_, texte, operation, trajet, temps, visite_id),
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
                    SELECT id, visite_id, type, texte, operation, trajet, temps_estime_min,
                           ordre, created_at, updated_at, valide_at
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
        if "operation" in data:
            sets.append("operation = %s"); params.append((data.get("operation") or "").strip() or None)
        if "trajet" in data:
            sets.append("trajet = %s"); params.append((data.get("trajet") or "").strip() or None)
        if "temps_estime_min" in data:
            t = data.get("temps_estime_min")
            if t in (None, ""):
                params.append(None)
            else:
                try:
                    params.append(int(t))
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="temps_estime_min doit être un entier (minutes)")
            sets.append("temps_estime_min = %s")
        # Migration 011 — statut de validation, modifiable indépendamment.
        if "valide" in data:
            sets.append("valide_at = " + ("NOW()" if data.get("valide") else "NULL"))
        elif "texte" in data:
            # Contenu réédité → la relecture précédente ne vaut plus : on
            # repasse l'observation « à valider ». Même règle que les notes.
            sets.append("valide_at = NULL")
        if not sets:
            raise HTTPException(status_code=400, detail="Aucun champ modifiable (type/texte/operation/trajet/temps_estime_min/valide)")
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
                    RETURNING o.id, o.visite_id, o.type, o.texte, o.operation, o.trajet,
                              o.temps_estime_min, o.ordre, o.created_at, o.updated_at,
                              o.valide_at
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
