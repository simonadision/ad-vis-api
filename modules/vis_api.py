"""
Ad VIS — routes métier (Sprint 0) : proxy projets HUB + CRUD visites.

Sprint 0 = fondations : sélection d'un projet HUB, choix du profil
(estimateur / contremaître), création d'une visite (brouillon, date du jour),
liste et reprise des visites d'un projet. Observations / photos / checklist =
Sprints 1-2 (tables déjà créées en migration, endpoints à venir).

Sécurité multi-tenant : organization_id est TOUJOURS pris du JWT (jamais du
payload). Les lectures/écritures sont scopées organization_id.
"""
import datetime
import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from psycopg.rows import dict_row

from modules import hub_service

logger = logging.getLogger("vis_api")

PROFILS = ("estimateur", "contremaitre")
STATUTS = ("brouillon", "finalise")


def _serialize_visite(r: dict) -> dict:
    return {
        "id": r["id"],
        "central_project_id": r["central_project_id"],
        "organization_id": str(r["organization_id"]),
        "profil": r["profil"],
        "auteur_user_id": r["auteur_user_id"],
        "auteur_nom": r.get("auteur_nom"),
        "date_visite": r["date_visite"].isoformat() if r.get("date_visite") else None,
        "statut": r["statut"],
        "meteo": r.get("meteo"),
        "nom_responsable": r.get("nom_responsable"),
        "poste_responsable": r.get("poste_responsable"),
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
    }


def register_vis_routes(get_conn, jwt_user):
    router = APIRouter()

    # ── Projets LIÉS à Ad VIS (opt-in) — écran de sélection ─────────────────
    @router.get("/v2/ad-hub-projects")
    def ad_hub_projects(user=Depends(jwt_user)):
        """Projets LIÉS à Ad VIS (opt-in) — plus toute la liste du hub.

        Simon 2026-08-14 : « si j'ouvre un projet dans Ad HUB, 1 chance sur 10
        que ce projet soit réalisé ». Cette route était un proxy direct vers
        GET {hub}/api/projects : tous les projets de l'organisation
        s'affichaient dans Ad VIS sans que personne ne les y ait mis. Pire, la
        liste encombrée poussait à la « nettoyer » — c'est ce réflexe qui a
        détruit six projets de production le 2026-08-13.

        Désormais elle lit ad_vis.vis_projects (migration 012). L'identité est
        rafraîchie depuis le hub quand il répond ; sinon on sert le cache local
        pour que l'écran reste lisible sur un chantier mal couvert.
        """
        org_id = user.get("organization_id")
        if not org_id:
            return {"projects": []}

        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                """
                SELECT central_project_id, name, code, client_name
                  FROM ad_vis.vis_projects
                 WHERE organization_id = %s AND deleted_at IS NULL
                 ORDER BY created_at DESC
                """,
                (org_id,),
            )
            liens = cur.fetchall()
        finally:
            conn.close()

        if not liens:
            return {"projects": []}

        # Identité fraîche = ENRICHISSEMENT seulement. Le hub ne peut plus
        # ajouter de cartes ici, il ne fait que corriger les libellés.
        frais: dict = {}
        try:
            for p in hub_service.list_org_projects(user["_jwt"]):
                if p.get("id") is not None:
                    frais[int(p["id"])] = p
        except Exception:
            logger.warning("[ad-vis] hub injoignable - ecran servi depuis le cache local")

        hub_ok = bool(frais)
        projects = []
        for lien in liens:
            pid = int(lien["central_project_id"])
            hp = frais.get(pid)
            # Lien vers un projet que le hub ne connaît plus (supprimé côté
            # Ad HUB, ou hors périmètre de l'user) : on ne l'affiche pas. On ne
            # le fait QUE si le hub a répondu — sinon une panne réseau viderait
            # l'écran d'un inspecteur en pleine visite.
            if hp is None:
                if hub_ok:
                    continue
                hp = {}
            projects.append({
                "id": pid,
                "name": hp.get("name") or lien.get("name") or "",
                "code": hp.get("code") or lien.get("code") or "",
                "statut": hp.get("statut"),
                "client_nom": hp.get("client_nom") or lien.get("client_name") or "",
            })
        return {"projects": projects}

    # ── Catalogue Ad HUB pour la modale « Nouveau projet » ───────────────────
    # ORDRE DES ROUTES : /available AVANT tout /{project_id}. FastAPI résout
    # dans l'ordre de déclaration ; un `/{project_id}` déclaré plus haut
    # avalerait « available » et tenterait de l'interpréter comme un entier.
    @router.get("/v2/ad-hub-projects/available")
    def ad_hub_projects_available(user=Depends(jwt_user)):
        """Catalogue Ad HUB dans lequel la modale vient piocher.

        `already_imported` distingue « lier » de « rouvrir », pour qu'un même
        projet ne soit jamais lié deux fois.
        """
        org_id = user.get("organization_id")
        if not org_id:
            return {"projects": [], "available": 0, "imported": 0,
                    "libelles": {}, "sections": []}

        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                "SELECT central_project_id FROM ad_vis.vis_projects "
                "WHERE organization_id = %s AND deleted_at IS NULL",
                (org_id,),
            )
            deja = {int(r["central_project_id"]) for r in cur.fetchall()}
        finally:
            conn.close()

        try:
            hub = hub_service.list_org_projects(user["_jwt"])
        except hub_service.HubServiceError as e:
            raise HTTPException(status_code=e.status_code or 502, detail=f"Ad HUB : {e.detail}")

        projets = [
            {
                "id": p.get("id"),
                "name": p.get("name") or p.get("nom") or "",
                "code": p.get("code") or "",
                "client_nom": p.get("client_nom") or "",
                "already_imported": int(p["id"]) in deja,
            }
            for p in hub if p.get("id") is not None
        ]
        importes = sum(1 for p in projets if p["already_imported"])
        # Libellés de l'arborescence : la modale reconstruit l'arbre depuis les
        # codes et a besoin des noms complets. Best-effort — sans eux elle
        # affiche les segments bruts, elle ne casse pas.
        arbo = hub_service.fetch_libelles_arborescence(user["_jwt"])
        return {
            "projects": projets,
            "available": len(projets) - importes,
            "imported": importes,
            "libelles": arbo["libelles"],
            "sections": arbo["sections"],
        }

    # ── Liaison d'un projet Ad HUB à Ad VIS (le geste explicite) ─────────────
    @router.post("/v2/ad-hub-projects/link", status_code=201)
    def link_ad_hub_project(data: dict = Body(...), user=Depends(jwt_user)):
        """Lie un projet Ad HUB à Ad VIS. Idempotent : relier ne duplique pas.

        C'est la SEULE porte d'entrée d'un projet dans Ad VIS depuis le
        catalogue du hub (la création via `/vis/projets/create-via-hub` reste
        l'autre chemin, pour un chantier qui n'existe pas encore).
        """
        org_id = user.get("organization_id")
        if not org_id:
            raise HTTPException(status_code=401, detail="organization_id absent du JWT")

        brut = data.get("central_project_id", data.get("ad_hub_project_id"))
        try:
            pid = int(brut)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="central_project_id requis (entier)")

        # Cloisonnement : le projet doit appartenir à l'org du JWT. Le hub est
        # seul juge — Ad VIS ne décide pas des droits, il les vérifie.
        if not hub_service.hub_project_belongs_to_org(user["_jwt"], pid):
            raise HTTPException(
                status_code=404,
                detail="Projet introuvable dans Ad HUB pour votre organisation.",
            )

        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                "SELECT id FROM ad_vis.vis_projects "
                "WHERE organization_id = %s AND central_project_id = %s "
                "AND deleted_at IS NULL",
                (org_id, pid),
            )
            if cur.fetchone():
                return {"project": {"central_project_id": pid}, "idempotent": True}

            fiche = {}
            try:
                fiche = hub_service.fetch_hub_project(user["_jwt"], pid) or {}
            except Exception:
                # Le cache d'affichage est un confort, pas une condition :
                # une liaison ne doit pas échouer parce qu'un libellé manque.
                logger.warning("[ad-vis] cache identite indisponible pour le projet %s", pid)

            cur.execute(
                """
                INSERT INTO ad_vis.vis_projects
                       (organization_id, central_project_id, name, code,
                        client_name, linked_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    org_id, pid,
                    fiche.get("name") or fiche.get("nom"),
                    fiche.get("code") or fiche.get("code_court"),
                    fiche.get("client_nom") or fiche.get("client_name"),
                    user.get("email"),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        return {"project": {"central_project_id": pid}, "idempotent": False}

    # ── Retrait d'un projet de la liste Ad VIS (PAS une suppression) ─────────
    @router.delete("/v2/ad-hub-projects/{project_id}", status_code=200)
    def retirer_du_module(project_id: int, user=Depends(jwt_user)):
        """Retire le projet de l'ÉCRAN Ad VIS — la fiche hub reste intacte.

        INCIDENT 2026-08-13 : cette route appelait le soft-delete d'Ad HUB.
        Nettoyer sa liste Ad VIS effaçait donc les projets de TOUS les modules.
        Six projets de production ont été perdus ce jour-là par ce chemin.

        Règle désormais absolue : un projet ne se supprime que depuis Ad HUB.

        2026-08-14 : le retrait est devenu PUREMENT LOCAL (soft-delete de la
        ligne ad_vis.vis_projects, migration 012). Ad VIS n'écrit plus rien
        dans le hub pour gérer son propre écran — un module qui n'écrit pas
        chez le voisin ne peut pas casser le voisin. L'étape intermédiaire
        (PATCH `modules_actifs.ad_vis`) n'a plus lieu d'être.

        Les visites ne bougent pas : ad_vis.visites.central_project_id porte
        l'id hub sans FK vers la table de liaison. Un projet délié puis relié
        retrouve ses visites, ses photos et ses observations.

        La route garde le verbe DELETE pour ne pas casser les clients
        déployés ; c'est son EFFET qui change, et le corps de réponse le dit
        (`retire_du_module` plutôt que `deleted`).
        """
        org_id = user.get("organization_id")
        if not org_id:
            raise HTTPException(status_code=401, detail="organization_id absent du JWT")

        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE ad_vis.vis_projects SET deleted_at = now(), updated_at = now() "
                "WHERE organization_id = %s AND central_project_id = %s "
                "AND deleted_at IS NULL",
                (org_id, project_id),
            )
            touche = cur.rowcount
            conn.commit()
        finally:
            conn.close()

        return {
            "retire_du_module": True,
            "deleted": False,
            "id": project_id,
            "etait_lie": touche > 0,
        }

    # ── Aperçu du code projet (gabarit de l'org) — modale « + Nouveau projet » ──
    @router.get("/v2/codification-preview")
    def codification_preview(user=Depends(jwt_user)):
        """Aperçu illustratif du prochain code projet (gabarit de l'org, proxy HUB)."""
        try:
            data = hub_service.get_codification(user["_jwt"])
        except hub_service.HubServiceError as e:
            raise HTTPException(status_code=e.status_code or 502, detail=f"Ad HUB : {e.detail}")
        return {"preview": data.get("preview"), "codification": data.get("codification")}

    # ── POST création MINIMALE d'un projet (nom + type_mandat) via le HUB ──────
    # La fiche projet vit dans Ad HUB : les visites référencent central_project_id
    # (= id hub). On crée donc la fiche là-bas et on renvoie son id + code
    # (auto-généré par le gabarit de l'org) pour démarrer la visite immédiatement.
    # Depuis l'opt-in (migration 012), on LIE aussi le projet à Ad VIS dans la
    # foulée : créer un chantier depuis Ad VIS est le geste explicite le plus
    # fort qui soit, il serait absurde d'obliger à repasser par la modale de
    # liaison pour retrouver, au retour, un projet qu'on vient d'ouvrir ici.
    _VIS_TYPE_MANDATS = ("soumission", "budget", "services")

    @router.post("/vis/projets/create-via-hub", status_code=201)
    def create_projet_via_hub(data: dict = Body(...), user=Depends(jwt_user)):
        name = (data.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="Nom du projet requis")
        type_mandat = (data.get("type_mandat") or "").strip() or None
        if type_mandat and type_mandat not in _VIS_TYPE_MANDATS:
            raise HTTPException(status_code=422,
                                detail=f"type_mandat invalide (valeurs : {', '.join(_VIS_TYPE_MANDATS)})")
        body = {"name": name, "type_mandat": type_mandat, "source": "ad_vis"}
        # Synchrone : l'utilisateur attend de pouvoir démarrer sa visite. Hub down /
        # rejet -> 502 avec message clair (le front réessaie, l'app n'est pas bloquée).
        try:
            proj = hub_service.create_project(user["_jwt"], body)
        except hub_service.HubServiceError as e:
            raise HTTPException(status_code=502, detail=f"Échec création projet dans Ad HUB : {e.detail}")

        # Liaison locale immédiate (voir le commentaire du bloc). Best-effort :
        # la fiche existe déjà côté hub, un échec d'écriture ici ne doit pas
        # transformer une création réussie en erreur. Au pire le projet se
        # rattrape par la modale « Nouveau projet ».
        org_id = user.get("organization_id")
        pid = proj.get("id")
        if org_id and pid is not None:
            try:
                conn = get_conn()
                try:
                    cur = conn.cursor()
                    cur.execute(
                        """
                        INSERT INTO ad_vis.vis_projects
                               (organization_id, central_project_id, name, code,
                                client_name, linked_by)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (org_id, int(pid), proj.get("name"), proj.get("code"),
                         proj.get("client_nom"), user.get("email")),
                    )
                    conn.commit()
                finally:
                    conn.close()
            except Exception:
                logger.warning("[ad-vis] liaison locale echouee pour le projet %s", pid)

        return {"project": {
            "id": proj.get("id"), "name": proj.get("name"),
            "code": proj.get("code"), "statut": proj.get("statut"),
            "client_nom": proj.get("client_nom"),
        }}

    # ── Proxy : employés + postes de l'org (modale création, Brique B) ─────
    @router.get("/v2/org-users")
    def org_users(user=Depends(jwt_user)):
        """Users de l'org (proxy HUB). Org-scopé par le JWT (le HUB filtre)."""
        try:
            users = hub_service.list_org_users(user["_jwt"])
        except hub_service.HubServiceError as e:
            raise HTTPException(status_code=e.status_code or 502, detail=f"Ad HUB : {e.detail}")
        return {"users": [
            {"id": u.get("id"), "nom": u.get("nom"), "email": u.get("email"),
             "fonction_nom": u.get("fonction_nom"), "status": u.get("status")}
            for u in users
        ]}

    @router.get("/v2/org-fonctions")
    def org_fonctions(user=Depends(jwt_user)):
        """Référentiel de postes/fonctions de l'org (proxy HUB) — suggestions."""
        try:
            fonctions = hub_service.list_fonctions(user["_jwt"])
        except hub_service.HubServiceError as e:
            raise HTTPException(status_code=e.status_code or 502, detail=f"Ad HUB : {e.detail}")
        return {"fonctions": [{"id": f.get("id"), "nom": f.get("nom")} for f in fonctions]}

    # ── POST création d'une visite (brouillon) ─────────────────────────────
    @router.post("/api/visites", status_code=201)
    def create_visite(data: dict = Body(...), user=Depends(jwt_user)):
        central_project_id = data.get("central_project_id")
        if central_project_id in (None, ""):
            raise HTTPException(status_code=400, detail="central_project_id requis")
        try:
            central_project_id = int(central_project_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="central_project_id invalide")

        # profil NEUTRALISÉ (checklist commune) : défaut serveur si la modale ne
        # l'envoie plus. Reste NOT NULL en base ; ne filtre plus la checklist.
        profil = data.get("profil") or "estimateur"
        if profil not in PROFILS:
            raise HTTPException(status_code=400, detail=f"profil invalide (valeurs : {', '.join(PROFILS)})")

        date_visite = data.get("date_visite")  # 'YYYY-MM-DD' optionnel
        if date_visite:
            try:
                datetime.date.fromisoformat(date_visite)
            except ValueError:
                raise HTTPException(status_code=400, detail="date_visite invalide (attendu YYYY-MM-DD)")
        meteo = (data.get("meteo") or None)
        nom_resp = (data.get("nom_responsable") or "").strip() or None
        poste_resp = (data.get("poste_responsable") or "").strip() or None

        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                cur.execute(
                    """
                    INSERT INTO ad_vis.visites
                      (central_project_id, organization_id, profil, auteur_user_id,
                       date_visite, statut, meteo, nom_responsable, poste_responsable)
                    VALUES (%s, %s, %s, %s, COALESCE(%s::date, CURRENT_DATE), 'brouillon', %s, %s, %s)
                    RETURNING id, central_project_id, organization_id, profil, auteur_user_id,
                              date_visite, statut, meteo, nom_responsable, poste_responsable, created_at
                    """,
                    (central_project_id, user["organization_id"], profil, user["id"],
                     date_visite, meteo, nom_resp, poste_resp),
                )
                row = cur.fetchone()
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()
        return {"visite": _serialize_visite(row)}

    # ── GET liste des visites d'un projet (org-scopé) ──────────────────────
    @router.get("/api/visites")
    def list_visites(
        project_id: int = Query(..., description="central_project_id du projet HUB"),
        user=Depends(jwt_user),
    ):
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                cur.execute(
                    """
                    SELECT v.id, v.central_project_id, v.organization_id, v.profil,
                           v.auteur_user_id, u.nom AS auteur_nom, v.date_visite,
                           v.statut, v.meteo, v.nom_responsable, v.poste_responsable, v.created_at
                    FROM ad_vis.visites v
                    LEFT JOIN ad_vis.users u ON u.id = v.auteur_user_id
                    WHERE v.organization_id = %s AND v.central_project_id = %s
                      AND v.deleted_at IS NULL
                    ORDER BY v.date_visite DESC, v.id DESC
                    """,
                    (user["organization_id"], project_id),
                )
                rows = cur.fetchall()
            finally:
                cur.close()
        finally:
            conn.close()
        return {"visites": [_serialize_visite(r) for r in rows], "total": len(rows)}

    # ── GET détail d'une visite (reprendre un brouillon) ───────────────────
    @router.get("/api/visites/{visite_id}")
    def get_visite(visite_id: int, user=Depends(jwt_user)):
        row = _fetch_visite_scoped(get_conn, visite_id, user["organization_id"])
        if not row:
            raise HTTPException(status_code=404, detail="Visite introuvable")
        return {"visite": _serialize_visite(row)}

    # ── PATCH visite (statut / météo) ──────────────────────────────────────
    @router.patch("/api/visites/{visite_id}")
    def update_visite(visite_id: int, data: dict = Body(...), user=Depends(jwt_user)):
        sets, params = [], []
        if "statut" in data:
            if data["statut"] not in STATUTS:
                raise HTTPException(status_code=400, detail="statut invalide")
            sets.append("statut = %s"); params.append(data["statut"])
        if "meteo" in data:
            sets.append("meteo = %s"); params.append(data.get("meteo") or None)
        if "date_visite" in data and data["date_visite"]:
            try:
                datetime.date.fromisoformat(data["date_visite"])
            except ValueError:
                raise HTTPException(status_code=400, detail="date_visite invalide (YYYY-MM-DD)")
            sets.append("date_visite = %s"); params.append(data["date_visite"])
        if "nom_responsable" in data:
            sets.append("nom_responsable = %s"); params.append((data.get("nom_responsable") or "").strip() or None)
        if "poste_responsable" in data:
            sets.append("poste_responsable = %s"); params.append((data.get("poste_responsable") or "").strip() or None)
        if not sets:
            raise HTTPException(status_code=400, detail="Aucun champ modifiable (statut/meteo/date_visite/nom_responsable/poste_responsable)")

        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                cur.execute(
                    f"""
                    UPDATE ad_vis.visites SET {', '.join(sets)}
                    WHERE id = %s AND organization_id = %s
                    RETURNING id, central_project_id, organization_id, profil, auteur_user_id,
                              date_visite, statut, meteo, nom_responsable, poste_responsable, created_at
                    """,
                    params + [visite_id, user["organization_id"]],
                )
                row = cur.fetchone()
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Visite introuvable")
        return {"visite": _serialize_visite(row)}

    # ── DELETE visite = SOFT-delete réversible + cascade soft-delete contenus ─
    @router.delete("/api/visites/{visite_id}")
    def delete_visite(visite_id: int, user=Depends(jwt_user)):
        """Soft-delete (deleted_at) — JAMAIS de hard-delete. Org-scopé via la
        visite (404 cross-org). Cascade soft-delete des contenus liés (notes,
        observations, photos, réponses checklist) DANS LA MÊME TRANSACTION : NOW()
        y est constant → même deleted_at pour la visite et ses contenus (permet une
        restauration précise par super_admin). NE TOUCHE PAS les objets R2 des
        photos (conservés) ni le HUB/GED (enregistrement officiel indépendant)."""
        org = user["organization_id"]
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                # 1) la visite (idempotent : seulement si pas déjà supprimée)
                cur.execute(
                    """
                    UPDATE ad_vis.visites SET deleted_at = NOW()
                    WHERE id = %s AND organization_id = %s AND deleted_at IS NULL
                    RETURNING id
                    """,
                    (visite_id, org),
                )
                if cur.fetchone() is None:
                    raise HTTPException(status_code=404, detail="Visite introuvable")
                # 2) cascade sur les contenus (R2 et HUB/GED NON touchés)
                cascade = {}
                for table in ("notes", "observations", "photos", "checklist_reponses"):
                    cur.execute(
                        f"UPDATE ad_vis.{table} SET deleted_at = NOW() "
                        f"WHERE visite_id = %s AND deleted_at IS NULL",
                        (visite_id,),
                    )
                    cascade[table] = cur.rowcount
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()
        return {"deleted": True, "id": visite_id, "cascade": cascade}

    return router


def _fetch_visite_scoped(get_conn, visite_id: int, org_id):
    conn = get_conn()
    try:
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute(
                """
                SELECT v.id, v.central_project_id, v.organization_id, v.profil,
                       v.auteur_user_id, u.nom AS auteur_nom, v.date_visite,
                       v.statut, v.meteo, v.nom_responsable, v.poste_responsable, v.created_at
                FROM ad_vis.visites v
                LEFT JOIN ad_vis.users u ON u.id = v.auteur_user_id
                WHERE v.id = %s AND v.organization_id = %s AND v.deleted_at IS NULL
                """,
                (visite_id, org_id),
            )
            return cur.fetchone()
        finally:
            cur.close()
    finally:
        conn.close()
