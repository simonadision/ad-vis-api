"""
Ad VIS — Service Cloudflare R2 (storage S3-compatible). Réplique du pattern
adision-app-api/modules/r2_service.py, adapté aux PHOTOS de visite.

Mode SERVER-MEDIATED : la tablette POST le fichier (multipart) à ad-vis-api qui
écrit lui-même sur R2 (put_object). L'affichage se fait via une URL GET signée
INLINE (galerie <img>). Bucket partagé avec le HUB (adision-documents), préfixe
distinct `advis/<org>/<visite>/...`.

Env requis (mêmes que le HUB) :
  R2_ACCESS_KEY_ID · R2_SECRET_ACCESS_KEY · R2_ENDPOINT_URL · R2_BUCKET_NAME
"""
from __future__ import annotations

import os
from urllib.parse import quote

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

DEFAULT_BUCKET = "adision-documents"
DEFAULT_PRESIGN_EXPIRES = 600  # 10 min


def _bucket_name() -> str:
    return (os.environ.get("R2_BUCKET_NAME") or DEFAULT_BUCKET).strip()


def get_s3_client():
    """Client boto3 R2. Lève RuntimeError si une env R2_* manque. À appeler
    PAR REQUÊTE (pas au module-load) pour ne pas crasher au boot sans creds.
    .strip() défensif (newline collé dans Railway → 'Invalid endpoint')."""
    access_key = (os.environ.get("R2_ACCESS_KEY_ID") or "").strip()
    secret_key = (os.environ.get("R2_SECRET_ACCESS_KEY") or "").strip()
    endpoint = (os.environ.get("R2_ENDPOINT_URL") or "").strip()
    if not (access_key and secret_key and endpoint):
        missing = [n for n, v in [
            ("R2_ACCESS_KEY_ID", access_key), ("R2_SECRET_ACCESS_KEY", secret_key),
            ("R2_ENDPOINT_URL", endpoint),
        ] if not v]
        raise RuntimeError(f"R2 non configuré — env vars manquantes : {', '.join(missing)}")
    return boto3.client(
        "s3", endpoint_url=endpoint,
        aws_access_key_id=access_key, aws_secret_access_key=secret_key,
        region_name="auto",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def r2_configured() -> bool:
    return all((os.environ.get(k) or "").strip() for k in
               ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT_URL"))


def put_bytes(r2_key: str, data: bytes, content_type: str) -> int:
    """Upload SERVEUR de bytes vers R2 (put_object). Idempotent (PUT écrase).
    Retourne la taille."""
    s3 = get_s3_client()
    try:
        s3.put_object(Bucket=_bucket_name(), Key=r2_key, Body=data, ContentType=content_type)
    except ClientError as e:
        raise RuntimeError(f"Échec upload R2 : {e}") from e
    return len(data)


def generate_view_url(r2_key: str, expires_in: int = DEFAULT_PRESIGN_EXPIRES) -> str:
    """URL GET signée INLINE pour afficher la photo dans une <img>
    (Content-Disposition: inline, pas attachment)."""
    s3 = get_s3_client()
    try:
        return s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": _bucket_name(), "Key": r2_key, "ResponseContentDisposition": "inline"},
            ExpiresIn=expires_in,
        )
    except ClientError as e:
        raise RuntimeError(f"Échec génération URL view : {e}") from e


def generate_download_url(r2_key: str, filename: str, expires_in: int = DEFAULT_PRESIGN_EXPIRES) -> str:
    """URL GET signée attachment (téléchargement avec nom de fichier)."""
    s3 = get_s3_client()
    safe = quote(filename, safe="")
    disposition = f'attachment; filename="{filename}"; filename*=UTF-8\'\'{safe}'
    try:
        return s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": _bucket_name(), "Key": r2_key, "ResponseContentDisposition": disposition},
            ExpiresIn=expires_in,
        )
    except ClientError as e:
        raise RuntimeError(f"Échec génération URL download : {e}") from e


def get_bytes(r2_key: str) -> bytes:
    """Récupère les bytes d'un objet R2 côté SERVEUR (get_object) — utilisé pour
    intégrer les photos dans le PDF sans passer par une URL signée publique."""
    s3 = get_s3_client()
    try:
        resp = s3.get_object(Bucket=_bucket_name(), Key=r2_key)
        return resp["Body"].read()
    except ClientError as e:
        raise RuntimeError(f"Échec lecture R2 : {e}") from e


def delete_object(r2_key: str) -> bool:
    """Suppression PHYSIQUE R2 — NON appelée par le soft-delete (réversibilité).
    Réservée à une purge admin différée (rétention)."""
    s3 = get_s3_client()
    try:
        s3.delete_object(Bucket=_bucket_name(), Key=r2_key)
        return True
    except ClientError as e:
        raise RuntimeError(f"Échec suppression R2 : {e}") from e
