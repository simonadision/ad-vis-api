"""
Adision Ad VIS — API (visite de chantier pré-construction, tablette-first).

Module satellite de la suite Ad FLO. SSO JWT partagé (émis par le dashboard
adision-app-api). Schéma Postgres `ad_vis`. Sprint 0 = fondations : SSO +
proxy projets HUB + CRUD visites.
"""
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from modules.auth_jwt import make_jwt_deps
from modules.vis_api import register_vis_routes
from modules.capture_api import register_capture_routes
from modules.calendar_api import register_calendar_routes
from modules.photos_api import register_photos_routes
from modules.checklist_api import register_checklist_routes
from modules.report_pdf_api import register_report_routes

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/adision_vis",
)


def get_conn():
    # sslmode=require pour Railway, ignoré en local si non supporté.
    return psycopg.connect(DATABASE_URL, sslmode="require")


# ──────────────────────────────────────────────────────────────────────
# Bootstrap automatique de la BD au démarrage (réplique du pattern Ad VIU :
# rejoue toutes les migrations *.sql idempotentes par ordre alphabétique).
# ──────────────────────────────────────────────────────────────────────

def _log(msg: str):
    print(f"[migrations] {msg}", flush=True)
    sys.stdout.flush()


def _bootstrap_db():
    _log("─── démarrage bootstrap ───")
    if not os.environ.get("DATABASE_URL"):
        _log("DATABASE_URL absent — abort")
        return
    try:
        conn = get_conn()
        _log("connexion BD OK")
        conn.add_notice_handler(lambda diag: _log(f"  ↳ {diag.message_primary}"))
    except Exception as e:
        _log(f"ÉCHEC connexion BD : {type(e).__name__} : {e}")
        return
    try:
        migrations_dir = Path(__file__).parent / "migrations"
        _log(f"dossier migrations : {migrations_dir} (existe ? {migrations_dir.is_dir()})")
        if not migrations_dir.is_dir():
            _log("dossier migrations/ absent — abort")
            return
        files = sorted(migrations_dir.glob("*.sql"))
        _log(f"fichiers SQL trouvés ({len(files)})")
        # ── Phase K2-3 étape 2 (29 juin 2026) — tracking schema_migrations ──
        # Pattern verbatim du pilote est-api K2-3.1 (SHA 2b17dc8). Schéma
        # ad_vis (créé par mig 001).
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS ad_vis.schema_migrations ("
            "  filename TEXT PRIMARY KEY,"
            "  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            ")"
        )
        conn.commit()
        cur.execute("SELECT filename FROM ad_vis.schema_migrations")
        done = {r[0] for r in cur.fetchall()}
        cur.close()
        _log(f"schema_migrations : {len(done)} déjà appliquées / {len(files)} fichiers")
        for f in files:
            if f.name in done:
                continue
            sql = f.read_text(encoding="utf-8")
            _log(f"exécution de {f.name} ({len(sql)} chars)...")
            cur = conn.cursor()
            try:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO ad_vis.schema_migrations (filename) "
                    "VALUES (%s) ON CONFLICT (filename) DO NOTHING",
                    (f.name,),
                )
                conn.commit()
                _log(f"{f.name} ✓")
            except Exception as e:
                conn.rollback()
                _log(f"ERREUR sur {f.name} : {type(e).__name__} : {e}")
                raise
            finally:
                cur.close()
        _log("─── bootstrap terminé avec succès ───")
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[lifespan] startup hook fired", flush=True)
    # Phase K2-3 étape 2 — B3 boot fatal (pattern pilote est-api 2b17dc8).
    try:
        _bootstrap_db()
    except Exception as e:
        print(
            f"[lifespan] FATAL — bootstrap échoué, boot refusé : "
            f"{type(e).__name__} : {e}",
            flush=True,
        )
        raise
    print("[lifespan] startup complete, accepting requests", flush=True)
    yield
    print("[lifespan] shutdown", flush=True)


app = FastAPI(title="Adision Ad VIS API", lifespan=lifespan)

# CORS — dashboard + prod Ad VIS (vis.adision.ca + futur vis.adflo.ca) + dev local.
#
# Phase I étape 3 (juin 2026) — extrait en constante CORS_ALLOWED_ORIGINS
# pour DRY avec le middleware origin_guard. Inclut vis.adflo.ca/app.adflo.ca
# pour les flux ad-join cross-eTLD.
CORS_ALLOWED_ORIGINS = [
    "https://app.adision.ca",
    "https://vis.adision.ca",
    "https://vis.adflo.ca",      # migration domaine (chantier week-end)
    "https://app.adflo.ca",
    "http://localhost:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Phase I étape 3 — middleware Origin/Referer CSRF (ENFORCE) ──────
# Pattern identique au hub (SHA 9c58e94). Discriminant cookie SSO.
from modules.origin_guard import install_origin_guard
install_origin_guard(app, cors_allowed_origins=CORS_ALLOWED_ORIGINS, log_only=False)

# ── LEÇON 3 K4 (29 juin 2026) — filet CORS sur exception 500 ─────────
# Pattern copié verbatim du hub adision-app-api (SHA cd2d07d), api-bud
# (SHA 8cba838), viu (SHA 9193c07). Garantit que toute exception non
# gérée produit une réponse 500 AVEC les headers CORS (au lieu de
# ERR_FAILED muet côté browser). L'erreur reste 500, le traceback est
# loggué — le filet rend juste l'erreur LISIBLE.
#
# Indépendant de tout kill-switch / check de révocation. Préventif.
import re as _re_for_cors
import traceback as _tb_for_cors
from fastapi import Request
from fastapi.responses import JSONResponse as _JSONResponse_for_cors

_CORS_RE = _re_for_cors.compile(r"^https://([a-z0-9-]+\.)?adision\.ca$")
_CORS_EXPLICIT = set(CORS_ALLOWED_ORIGINS)

def _cors_headers_for_origin(origin: str | None) -> dict:
    if not origin:
        return {}
    if origin in _CORS_EXPLICIT or _CORS_RE.match(origin):
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        }
    return {}

@app.exception_handler(Exception)
async def _cors_aware_500_handler(request: Request, exc: Exception):
    """Filet structurel — toute exception non gérée → 500 JSON AVEC CORS.
    Log le traceback intégral pour ne RIEN cacher du bug réel."""
    print(
        f"[exception_handler] {request.method} {request.url.path} → "
        f"{type(exc).__name__}: {exc}\n{_tb_for_cors.format_exc()}",
        flush=True,
    )
    headers = _cors_headers_for_origin(request.headers.get("origin"))
    return _JSONResponse_for_cors(
        status_code=500,
        content={"detail": f"Internal Server Error: {type(exc).__name__}"},
        headers=headers,
    )

# JWT deps — souple Sprint 0 (jwt_user ne gate PAS le module ad_vis).
jwt_user, jwt_super_admin = make_jwt_deps(get_conn)

app.include_router(register_vis_routes(get_conn, jwt_user))
app.include_router(register_capture_routes(get_conn, jwt_user))
app.include_router(register_calendar_routes(get_conn, jwt_user))
app.include_router(register_photos_routes(get_conn, jwt_user))
app.include_router(register_checklist_routes(get_conn, jwt_user))
app.include_router(register_report_routes(get_conn, jwt_user))


@app.get("/")
def home():
    return {"service": "Adision Ad VIS", "status": "online", "docs": "/docs"}


@app.get("/health")
def health():
    """Diagnostic — BD + schéma ad_vis + présence JWT_SECRET (jamais sa valeur)."""
    db_ok = False
    db_error = None
    schema_ok = False
    visites_table_ok = False
    visites_count = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        db_ok = True
        cur.execute(
            "SELECT EXISTS(SELECT 1 FROM information_schema.schemata WHERE schema_name = 'ad_vis')"
        )
        schema_ok = bool(cur.fetchone()[0])
        cur.execute(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'ad_vis' AND table_name = 'visites')"
        )
        visites_table_ok = bool(cur.fetchone()[0])
        if visites_table_ok:
            cur.execute("SELECT COUNT(*) FROM ad_vis.visites")
            visites_count = int(cur.fetchone()[0])
        cur.close()
        conn.close()
    except Exception as e:
        db_error = str(e)[:200]
    secret = os.environ.get("JWT_SECRET") or ""
    return {
        "status": "ok" if (db_ok and visites_table_ok and bool(secret)) else "degraded",
        "service": "adision-vis-api",
        "database": "connected" if db_ok else "unreachable",
        "database_error": db_error,
        "schema_ad_vis_exists": schema_ok,
        "visites_table_exists": visites_table_ok,
        "visites_count": visites_count,
        "jwt_secret_configured": bool(secret),
        "jwt_secret_length": len(secret) if secret else 0,
    }


@app.get("/auth/me")
def auth_me(user=Depends(jwt_user)):
    """Valide le JWT et renvoie le user local + flag is_admin (touche
    last_login_at, best-effort). Le front s'en sert pour le bootstrap SSO."""
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE ad_vis.users SET last_login_at = NOW() WHERE id = %s", (user["id"],))
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass
    safe = {k: v for k, v in user.items() if k != "_jwt"}
    return {"user": safe, "is_admin": user.get("role") in ("admin", "super_admin")}
