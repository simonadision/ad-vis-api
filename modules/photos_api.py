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
from fastapi.responses import Response
from psycopg.rows import dict_row

from modules import r2_service

# Types image acceptés -> extension de clé R2.
ALLOWED_MIME = {
    "image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png",
    "image/webp": "webp", "image/heic": "heic", "image/heif": "heif",
}
# Réciproque extension -> mime (endpoint bytes : Content-Type du proxy R2).
EXT_MIME = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "webp": "image/webp", "heic": "image/heic", "heif": "image/heif",
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
        "annotated_from_photo_id": r.get("annotated_from_photo_id"),
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
                # On ne renvoie que les « feuilles » : une photo non supprimée
                # SANS enfant annoté vivant. L'annotée masque donc son original.
                cur.execute(
                    """
                    SELECT p.id, p.visite_id, p.observation_id, p.url_r2, p.legende,
                           p.lat, p.lng, p.prise_le, p.ordre, p.annotated_from_photo_id
                    FROM ad_vis.photos p
                    WHERE p.visite_id = %s AND p.deleted_at IS NULL
                      AND NOT EXISTS (
                        SELECT 1 FROM ad_vis.photos c
                        WHERE c.annotated_from_photo_id = p.id AND c.deleted_at IS NULL)
                    ORDER BY p.ordre ASC, p.id ASC
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

    # ── Octets bruts (proxy R2 server-side) — source MÊME-CONTRÔLE pour le
    #    canvas d'annotation : évite le tainting cross-origin du presigned R2
    #    (sinon canvas.toBlob lèverait SecurityError). Org-scopé via la visite. ─
    @router.get("/api/photos/{photo_id}/bytes")
    def photo_bytes(photo_id: int, user=Depends(jwt_user)):
        org = user["organization_id"]
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                cur.execute(
                    """
                    SELECT p.url_r2 FROM ad_vis.photos p
                    JOIN ad_vis.visites v ON v.id = p.visite_id
                    WHERE p.id = %s AND v.organization_id = %s
                      AND p.deleted_at IS NULL AND v.deleted_at IS NULL
                    """,
                    (photo_id, org),
                )
                row = cur.fetchone()
            finally:
                cur.close()
        finally:
            conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Photo introuvable")
        key = row["url_r2"]
        ext = key.rsplit(".", 1)[-1].lower() if "." in key else "jpg"
        try:
            data = r2_service.get_bytes(key)
        except Exception:
            raise HTTPException(status_code=502, detail="Lecture R2 impossible")
        return Response(content=data, media_type=EXT_MIME.get(ext, "application/octet-stream"))

    # ── Upload de la version ANNOTÉE (aplatie) — nouvelle ligne liée à
    #    l'originale (annotated_from_photo_id). L'original n'est PAS touché. ─
    @router.post("/api/visites/{visite_id}/photos/{photo_id}/annotated", status_code=201)
    async def upload_annotated(
        visite_id: int,
        photo_id: int,
        file: UploadFile = File(...),
        legende: str = Form(""),
        user=Depends(jwt_user),
    ):
        if not r2_service.r2_configured():
            raise HTTPException(status_code=503, detail="Stockage photo (R2) non configuré côté serveur")
        mime = (file.content_type or "").lower().strip()
        ext = ALLOWED_MIME.get(mime)
        if not ext:
            raise HTTPException(status_code=400, detail=f"Type d'image non supporté ({mime or 'inconnu'}).")
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="Image annotée vide")
        if len(data) > MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"Image trop volumineuse (max {MAX_BYTES // (1024 * 1024)} Mo)")

        org = user["organization_id"]
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                _assert_visite_org(cur, visite_id, org)
                # Le parent doit appartenir à CETTE visite (org déjà validée) + vivant.
                cur.execute(
                    "SELECT id, legende, observation_id, lat, lng, prise_le, ordre "
                    "FROM ad_vis.photos WHERE id = %s AND visite_id = %s AND deleted_at IS NULL",
                    (photo_id, visite_id),
                )
                parent = cur.fetchone()
                if not parent:
                    raise HTTPException(status_code=404, detail="Photo d'origine introuvable")
                key = f"advis/{org}/{visite_id}/{uuid.uuid4().hex}.{ext}"
                r2_service.put_bytes(key, data, mime)  # R2 d'abord : si échec, pas de row DB
                leg = (legende or "").strip() or (parent["legende"] or "")
                cur.execute(
                    """
                    INSERT INTO ad_vis.photos
                      (visite_id, url_r2, legende, lat, lng, prise_le, ordre,
                       observation_id, annotated_from_photo_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, visite_id, observation_id, url_r2, legende, lat, lng,
                              prise_le, ordre, annotated_from_photo_id
                    """,
                    (visite_id, key, leg, parent["lat"], parent["lng"], parent["prise_le"],
                     parent["ordre"], parent["observation_id"], photo_id),
                )
                row = cur.fetchone()
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()
        return {"photo": _serialize(row, _view_url(row["url_r2"]))}

    return router
