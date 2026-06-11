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
        _log(f"fichiers SQL trouvés ({len(files)}) : {[f.name for f in files]}")
        for f in files:
            sql = f.read_text(encoding="utf-8")
            _log(f"exécution de {f.name} ({len(sql)} chars)...")
            cur = conn.cursor()
            try:
                cur.execute(sql)
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
    try:
        _bootstrap_db()
    except Exception as e:
        print(f"[lifespan] bootstrap failed (app boots anyway): {e}", flush=True)
    print("[lifespan] startup complete, accepting requests", flush=True)
    yield
    print("[lifespan] shutdown", flush=True)


app = FastAPI(title="Adision Ad VIS API", lifespan=lifespan)

# CORS — dashboard + prod Ad VIS (vis.adision.ca + futur vis.adflo.ca) + dev local.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://app.adision.ca",
        "https://vis.adision.ca",
        "https://vis.adflo.ca",      # migration domaine (chantier week-end)
        "https://app.adflo.ca",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# JWT deps — souple Sprint 0 (jwt_user ne gate PAS le module ad_vis).
jwt_user, jwt_super_admin = make_jwt_deps(get_conn)

app.include_router(register_vis_routes(get_conn, jwt_user))


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
