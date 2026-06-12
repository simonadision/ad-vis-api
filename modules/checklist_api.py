"""
Ad VIS — Sprint 1 Brique 3 : checklist de visite par profil.

Portée Sprint 1 : les items viennent du SEED par défaut (system, org NULL) selon
le profil de la visite. L'utilisateur COCHE/DÉCOCHE + note par item (table
ad_vis.checklist_reponses, upsert sur UNIQUE(visite_id, checklist_item_id)). Il ne
crée PAS ses propres items (différé). Pas de soft-delete (état pur, pas de contenu).

Isolation : org-scopé via la VISITE parente (JWT, jamais le client) — même pattern
404 + allowlist que Briques 1-2. L'item patché doit appartenir au profil de la
visite (system ou org de la visite), sinon 404.
"""
from fastapi import APIRouter, Body, Depends, HTTPException
from psycopg.rows import dict_row


def _visite_scoped(cur, visite_id, org_id):
    """Retourne (id, profil) de la visite si elle appartient à l'org, sinon None."""
    cur.execute(
        "SELECT id, profil FROM ad_vis.visites WHERE id = %s AND organization_id = %s AND deleted_at IS NULL",
        (visite_id, org_id),
    )
    return cur.fetchone()


def register_checklist_routes(get_conn, jwt_user):
    router = APIRouter()

    @router.get("/api/visites/{visite_id}/checklist")
    def get_checklist(visite_id: int, user=Depends(jwt_user)):
        """Checklist COMMUNE (fusion estimateur+contremaitre, migration 006) :
        items actifs system (org NULL) + items de l'org, SANS filtre par profil
        — la même liste pour toute visite — fusionnés avec l'état coché/note."""
        org = user["organization_id"]
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                vis = _visite_scoped(cur, visite_id, org)
                if not vis:
                    raise HTTPException(status_code=404, detail="Visite introuvable")
                cur.execute(
                    """
                    SELECT ci.id AS item_id, ci.label, ci.ordre, ci.is_system,
                           COALESCE(cr.coche, FALSE)   AS coche,
                           COALESCE(cr.commentaire, '') AS commentaire
                    FROM ad_vis.checklist_items ci
                    LEFT JOIN ad_vis.checklist_reponses cr
                      ON cr.checklist_item_id = ci.id AND cr.visite_id = %s
                         AND cr.deleted_at IS NULL
                    WHERE ci.active = TRUE
                      AND (ci.organization_id IS NULL OR ci.organization_id = %s)
                    ORDER BY ci.is_system DESC, ci.ordre ASC, ci.id ASC
                    """,
                    (visite_id, org),
                )
                rows = cur.fetchall()
            finally:
                cur.close()
        finally:
            conn.close()
        items = [
            {"item_id": r["item_id"], "label": r["label"], "ordre": r["ordre"],
             "coche": r["coche"], "commentaire": r["commentaire"]}
            for r in rows
        ]
        return {
            "profil": vis["profil"],
            "items": items,
            "total": len(items),
            "coches": sum(1 for i in items if i["coche"]),
        }

    @router.patch("/api/visites/{visite_id}/checklist/{item_id}")
    def patch_item(visite_id: int, item_id: int, data: dict = Body(...), user=Depends(jwt_user)):
        """Upsert de l'état d'un item (coche et/ou commentaire) pour la visite.
        Patch partiel : un champ absent conserve sa valeur courante."""
        if "coche" not in data and "commentaire" not in data:
            raise HTTPException(status_code=400, detail="Aucun champ modifiable (coche/commentaire)")
        org = user["organization_id"]
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                vis = _visite_scoped(cur, visite_id, org)
                if not vis:
                    raise HTTPException(status_code=404, detail="Visite introuvable")
                # Garde : item ACTIF + visible pour l'org (system ou org). PLUS de
                # filtre par profil (checklist commune) ; org-scoping conservé via
                # la visite (_visite_scoped) → isolation cross-org intacte (404).
                cur.execute(
                    """
                    SELECT id FROM ad_vis.checklist_items
                    WHERE id = %s AND active = TRUE
                      AND (organization_id IS NULL OR organization_id = %s)
                    """,
                    (item_id, org),
                )
                if cur.fetchone() is None:
                    raise HTTPException(status_code=404, detail="Item de checklist invalide")
                # État courant pour un patch partiel.
                cur.execute(
                    "SELECT coche, commentaire FROM ad_vis.checklist_reponses "
                    "WHERE visite_id = %s AND checklist_item_id = %s",
                    (visite_id, item_id),
                )
                cur_state = cur.fetchone() or {"coche": False, "commentaire": ""}
                new_coche = bool(data["coche"]) if "coche" in data else cur_state["coche"]
                new_comment = (data.get("commentaire") or "").strip() if "commentaire" in data else cur_state["commentaire"]
                cur.execute(
                    """
                    INSERT INTO ad_vis.checklist_reponses (visite_id, checklist_item_id, coche, commentaire)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (visite_id, checklist_item_id)
                    DO UPDATE SET coche = EXCLUDED.coche, commentaire = EXCLUDED.commentaire
                    RETURNING checklist_item_id AS item_id, coche, commentaire
                    """,
                    (visite_id, item_id, new_coche, new_comment),
                )
                row = cur.fetchone()
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()
        return {"item": {"item_id": row["item_id"], "coche": row["coche"], "commentaire": row["commentaire"]}}

    return router
