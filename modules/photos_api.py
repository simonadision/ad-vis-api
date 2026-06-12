"""
Ad VIS — Sprint 1 Brique 2 : photos dans la visite (upload R2 + légende +
géoloc + horodatage). Upload SERVER-MEDIATED (multipart → R2 put_object).

Isolation multi-tenant identique à Brique 1 : opérations cloisonnées par l'org
de la VISITE parente (issue du JWT, jamais du client). Soft-delete réversible
(deleted_at) — l'objet R2 N'EST PAS supprimé (réversibilité ; purge différée).
Horodatage `prise_le` = serveur (source de vérité). Géoloc lat/lng best-effort
si fournie par le client.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from psycopg.rows import dict_row

from modules import r2_service

# Types image acceptés -> extension de clé R2.
ALLOWED_MIME = {
    "image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png",
    "image/webp": "webp", "image/heic": "heic", "image/heif": "heif",
}
MAX_BYTES = 15 * 1024 * 1024  # 15 Mo


def _assert_visite_org(cur, visite_id, org_id):
    cur.execute("SELECT id FROM ad_vis.visites WHERE id = %s AND organization_id = %s AND deleted_at IS NULL", (visite_id, org_id))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Visite introuvable")


def _serialize(r, url=None):
    return {
        "id": r["id"], "visite_id": r["visite_id"], "observation_id": r.get("observation_id"),
        "legende": r["legende"], "lat": r.get("lat"), "lng": r.get("lng"),
        "prise_le": r["prise_le"].isoformat() if r.get("prise_le") else None,
        "ordre": r["ordre"], "url": url,
    }


def _view_url(key):
    try:
        return r2_service.generate_view_url(key)
    except Exception:
        return None


def register_photos_routes(get_conn, jwt_user):
    router = APIRouter()

    @router.post("/api/visites/{visite_id}/photos", status_code=201)
    async def upload_photo(
        visite_id: int,
        file: UploadFile = File(...),
        legende: str = Form(""),
        lat: Optional[float] = Form(None),
        lng: Optional[float] = Form(None),
        user=Depends(jwt_user),
    ):
        if not r2_service.r2_configured():
            raise HTTPException(status_code=503, detail="Stockage photo (R2) non configuré côté serveur")
        mime = (file.content_type or "").lower().strip()
        ext = ALLOWED_MIME.get(mime)
        if not ext:
            raise HTTPException(status_code=400, detail=f"Type d'image non supporté ({mime or 'inconnu'}). Acceptés : JPEG, PNG, WebP, HEIC.")
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="Fichier vide")
        if len(data) > MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"Fichier trop volumineux (max {MAX_BYTES // (1024 * 1024)} Mo)")

        org = user["organization_id"]
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                _assert_visite_org(cur, visite_id, org)
                # Chemin structuré org/visite ; nom opaque (uuid) -> pas de collision.
                key = f"advis/{org}/{visite_id}/{uuid.uuid4().hex}.{ext}"
                r2_service.put_bytes(key, data, mime)  # R2 d'abord : si échec, pas de row DB
                cur.execute(
                    """
                    INSERT INTO ad_vis.photos (visite_id, url_r2, legende, lat, lng, prise_le, ordre)
                    VALUES (%s, %s, %s, %s, %s, NOW(), COALESCE(
                      (SELECT MAX(ordre) + 1 FROM ad_vis.photos WHERE visite_id = %s AND deleted_at IS NULL), 0))
                    RETURNING id, visite_id, observation_id, url_r2, legende, lat, lng, prise_le, ordre
                    """,
                    (visite_id, key, (legende or "").strip(), lat, lng, visite_id),
                )
                row = cur.fetchone()
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()
        return {"photo": _serialize(row, _view_url(row["url_r2"]))}

    @router.get("/api/visites/{visite_id}/photos")
    def list_photos(visite_id: int, user=Depends(jwt_user)):
        org = user["organization_id"]
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                _assert_visite_org(cur, visite_id, org)
                cur.execute(
                    """
                    SELECT id, visite_id, observation_id, url_r2, legende, lat, lng, prise_le, ordre
                    FROM ad_vis.photos
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
        return {"photos": [_serialize(r, _view_url(r["url_r2"])) for r in rows], "total": len(rows)}

    @router.patch("/api/photos/{photo_id}")
    def patch_photo(photo_id: int, data: dict = Body(...), user=Depends(jwt_user)):
        if "legende" not in data:
            raise HTTPException(status_code=400, detail="Aucun champ modifiable (legende)")
        leg = (data.get("legende") or "").strip()
        org = user["organization_id"]
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                cur.execute(
                    """
                    UPDATE ad_vis.photos p SET legende = %s, updated_at = NOW()
                    FROM ad_vis.visites v
                    WHERE p.id = %s AND p.visite_id = v.id
                      AND v.organization_id = %s AND p.deleted_at IS NULL
                    RETURNING p.id, p.visite_id, p.observation_id, p.url_r2, p.legende, p.lat, p.lng, p.prise_le, p.ordre
                    """,
                    (leg, photo_id, org),
                )
                row = cur.fetchone()
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Photo introuvable")
        return {"photo": _serialize(row, _view_url(row["url_r2"]))}

    @router.delete("/api/photos/{photo_id}")
    def delete_photo(photo_id: int, user=Depends(jwt_user)):
        # Soft-delete : on N'EFFACE PAS l'objet R2 (réversibilité ; purge différée).
        org = user["organization_id"]
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                cur.execute(
                    """
                    UPDATE ad_vis.photos p SET deleted_at = NOW()
                    FROM ad_vis.visites v
                    WHERE p.id = %s AND p.visite_id = v.id
                      AND v.organization_id = %s AND p.deleted_at IS NULL
                    RETURNING p.id
                    """,
                    (photo_id, org),
                )
                row = cur.fetchone()
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Photo introuvable")
        return {"deleted": True, "id": photo_id}

    return router
