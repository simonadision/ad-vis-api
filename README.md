# Ad VIS — API (visite de chantier pré-construction)

Module satellite Ad FLO. FastAPI + Postgres (schéma `ad_vis`), SSO JWT partagé
(émis par le dashboard `adision-app-api`). **Sprint 0** : SSO souple + proxy
projets HUB + CRUD visites. Photos / observations / checklist = Sprints 1-2
(tables déjà créées en migration).

## Architecture
- **Frontend** : `adision-monorepo/apps/ad-vis` (React/Vite, `@adision/ui` `<AppHeader currentModule="ad_vis">`), déployé sur Vercel → `vis.adision.ca`.
- **Backend** : ce repo `ad-vis-api`, déployé sur Railway → URL publique.
- **DB** : Postgres dédié (Railway), schéma `ad_vis`, migrations rejouées à chaque boot (`_bootstrap_db`, idempotentes).
- **SSO** : `adision_jwt` (localStorage, propagé cross-subdomain via `?token=`). `auth_jwt.jwt_user` valide la signature ; **gating souple Sprint 0** (pas de check du module `ad_vis` — voir docstring `modules/auth_jwt.py`).
- **Projets HUB** : `GET /v2/ad-hub-projects` proxy le JWT vers `adision-app-api` `GET /api/projects` (le HUB valide l'`organization_id`).

## Endpoints (Sprint 0)
| Méthode | Route | Rôle |
|---|---|---|
| GET | `/health` | diagnostic (BD, schéma, JWT_SECRET) |
| GET | `/auth/me` | valide le JWT, renvoie le user (bootstrap SSO front) |
| GET | `/v2/ad-hub-projects` | liste des projets de l'org (proxy HUB) |
| POST | `/api/visites` | créer une visite (brouillon, date du jour) |
| GET | `/api/visites?project_id=` | visites d'un projet (org-scopé) |
| GET | `/api/visites/{id}` | détail (reprendre un brouillon) |
| PATCH | `/api/visites/{id}` | statut / météo / date |

## Variables d'environnement (Railway)
| Var | Obligatoire | Note |
|---|---|---|
| `DATABASE_URL` | ✅ | Postgres Railway (référence du service Postgres) |
| `JWT_SECRET` | ✅ | **Même valeur** que les autres services (app/viu/bud…) — sinon 401 |
| `JWT_SECRET_OLD` | optionnel | rotation gracieuse |
| `HUB_API_URL` | optionnel | défaut = `https://web-production-1c52e.up.railway.app` |

## Lancer en local
```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql://…   # ou un Postgres local
export JWT_SECRET=…                   # même secret que la suite
python -m uvicorn api:app --reload --port 8000
```

## Déploiement Railway (à faire — Simon)
1. `railway init` (nouveau projet, ex. `adision-vis`) puis ajouter un service Postgres : `railway add` → Postgres.
2. Lier ce repo au service web (GitHub) ou `railway up`.
3. Variables : `JWT_SECRET` (copier depuis un service existant), `DATABASE_URL` (référence Postgres). `HUB_API_URL` optionnel.
4. Le boot rejoue `migrations/*.sql` → schéma `ad_vis` créé. Vérifier `GET /health` = `status: ok`.

## Frontend + DNS (à faire — Simon)
1. Vercel : nouveau projet, **Root Directory = `apps/ad-vis`** (monorepo `adision-monorepo`), build `npm run build`, output `dist`.
2. Vars Vercel : `VITE_VIS_API_URL` = URL Railway de cette API ; `VITE_DASHBOARD_URL=https://app.adision.ca` ; `VITE_VIS_URL=https://vis.adision.ca`.
3. DNS GoDaddy : `CNAME vis → cname.vercel-dns.com` (comme les autres satellites).
4. **Activer la tuile** : dans `adision-monorepo/packages/ui/src/AdisionModules.js`, passer `ad_vis` `status: "soon"` → `"active"` (1 ligne), bump `@adision/ui`, redéployer les apps → la tuile Ad VIS devient cliquable dans le ModuleSwitcher de toute la suite.

## Migration vers adflo.ca
CORS inclut déjà `vis.adflo.ca` / `app.adflo.ca` — bascule au chantier week-end.
