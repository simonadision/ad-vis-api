"""
Ad VIS — Sprint 1 Brique 4 (portée A) : rapport de visite PDF (reportlab).

GET /api/visites/{id}/rapport.pdf -> PDF en download (attachment). Assemble TOUT
le contenu Sprint 1 d'UNE visite : en-tête (projet/profil/date/statut) · notes ·
observations groupées par type · photos (vignette R2 + légende + horodatage +
géoloc) · checklist (état + notes, progression X/N). Footer wordmark Ad FLO
(copié de adision-api/ad_budget_api.draw_adflo_footer). Portrait, FR.

Org-scopé via la visite parente (404 cross-org) + allowlist héritée de jwt_user.
AUCUNE écriture DB. Images = bytes R2 côté SERVEUR (pas d'URL signée publique).
Dépôt GED HUB = HORS scope (sujet dédié).
"""
import datetime
import hashlib
import io
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from psycopg.rows import dict_row

VISITE_CHANTIER_CAT = "Visite de chantier"
PHASE = "pre_construction"

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image as RLImage, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from modules import hub_service, r2_service

OBS_LABELS = {"acces": "Accès", "contrainte": "Contrainte", "etat_existant": "État existant",
              "question": "Question", "constat": "Constat"}
OBS_ORDER = ["acces", "contrainte", "etat_existant", "question", "constat"]
PROFIL_LABELS = {"estimateur": "Estimateur", "contremaitre": "Contremaître"}
STATUT_LABELS = {"brouillon": "Brouillon", "finalise": "Finalisée"}


def draw_adflo_footer(canvas, doc, nb=False):
    """Footer wordmark « Propulsé par A d FLO » — copié 1:1 d'Ad BUD
    (adision-api/ad_budget_api.draw_adflo_footer). Dessin vectoriel : aucun
    fichier logo requis. Centré, ~12 mm du bord inférieur."""
    C_RED, C_GREEN, C_BLUE = colors.HexColor("#ef4444"), colors.HexColor("#10b981"), colors.HexColor("#1e3a8a")
    C_INK, C_GREY = colors.HexColor("#111827"), colors.HexColor("#6b7280")
    canvas.saveState()
    cx = doc.pagesize[0] / 2.0
    y0 = 12 * mm
    k = 0.12
    SVG_H = 140.0
    base_y = y0 - k * (SVG_H - 112.0)

    def _ty(svg_y):
        return base_y + k * (SVG_H - svg_y)

    prefix = "Propulsé par "
    w_prefix = canvas.stringWidth(prefix, "Helvetica", 7)
    fs = 76.0 * k
    logo_w = k * 98.0 + canvas.stringWidth("FLO", "Helvetica-Bold", fs)
    start_x = cx - (w_prefix + logo_w) / 2.0
    lx = start_x + w_prefix

    def _rect(svg_x, svg_y, w, h, col):
        canvas.setFillColor(col)
        canvas.rect(lx + k * svg_x, base_y + k * (SVG_H - svg_y - h), k * w, k * h, fill=1, stroke=0)

    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(C_GREY if nb else colors.HexColor("#94a3b8"))
    canvas.drawString(start_x, y0, prefix)
    col_a = col_d = col_flo = C_INK
    col_x1 = col_x2 = col_x3 = C_GREY
    if not nb:
        col_a, col_d, col_flo = C_RED, C_GREEN, C_BLUE
        col_x1, col_x2, col_x3 = C_BLUE, C_RED, C_GREEN
    _rect(55, 0, 6, 27, col_x1)
    _rect(61, 27, 27, 6, col_x1)
    _rect(55, 33, 6, 27, col_x2)
    _rect(28, 27, 27, 6, col_x3)
    canvas.setFont("Helvetica-Bold", fs)
    canvas.setFillColor(col_a); canvas.drawString(lx + k * 0.0, _ty(112), "A")
    canvas.setFillColor(col_d); canvas.drawString(lx + k * 52.0, _ty(112), "d")
    canvas.setFillColor(col_flo); canvas.drawString(lx + k * 98.0, _ty(130), "FLO")
    canvas.restoreState()


def _build_pdf(vis, projet_nom, notes, obs, photos, checklist):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, title="Rapport de visite",
                            topMargin=2 * cm, bottomMargin=2.4 * cm, leftMargin=2 * cm, rightMargin=2 * cm)
    ss = getSampleStyleSheet()
    H1 = ParagraphStyle("H1", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=colors.HexColor("#1e3a8a"))
    SUB = ParagraphStyle("SUB", parent=ss["Normal"], fontSize=12, leading=16, textColor=colors.HexColor("#475569"))
    H2 = ParagraphStyle("H2", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=colors.HexColor("#0f172a"), spaceBefore=16, spaceAfter=6)
    BODY = ParagraphStyle("BODY", parent=ss["Normal"], fontSize=10.5, leading=15, textColor=colors.HexColor("#1f2937"))
    SMALL = ParagraphStyle("SMALL", parent=ss["Normal"], fontSize=9, leading=12, textColor=colors.HexColor("#6b7280"))
    TYPE = ParagraphStyle("TYPE", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=colors.HexColor("#1e3a8a"), spaceBefore=4)

    el = [Paragraph("Rapport de visite de chantier", H1), Paragraph(escape(projet_nom), SUB), Spacer(1, 10)]

    # profil NEUTRALISÉ (checklist commune) -> ligne « Profil » RETIRÉE de l'en-tête.
    info = [("Date de visite", str(vis["date_visite"])),
            ("Statut", STATUT_LABELS.get(vis["statut"], vis["statut"]))]
    # Responsable (nom + poste) — ligne OMISE proprement si les deux sont vides.
    resp_nom = (vis.get("nom_responsable") or "").strip()
    resp_poste = (vis.get("poste_responsable") or "").strip()
    if resp_nom or resp_poste:
        rv = f"{resp_nom} — {resp_poste}" if (resp_nom and resp_poste) else (resp_nom or resp_poste)
        info.append(("Responsable", rv))
    if vis.get("auteur_nom"):
        info.append(("Auteur", vis["auteur_nom"]))
    if vis.get("meteo"):
        info.append(("Météo", vis["meteo"]))
    t = Table([[Paragraph(f"<b>{escape(k)}</b>", SMALL), Paragraph(escape(str(v)), BODY)] for k, v in info],
              colWidths=[4 * cm, 12.6 * cm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                           ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#e5e7eb"))]))
    el.append(t)

    # Notes
    el.append(Paragraph("Notes", H2))
    if notes:
        for n in notes:
            el.append(Paragraph("• " + escape(n["texte"]), BODY))
    else:
        el.append(Paragraph("Aucune note.", SMALL))

    # Observations groupées par type
    el.append(Paragraph("Observations", H2))
    if obs:
        by = {}
        for o in obs:
            by.setdefault(o["type"], []).append(o["texte"])
        for ty in OBS_ORDER:
            if by.get(ty):
                el.append(Paragraph(OBS_LABELS[ty], TYPE))
                for txt in by[ty]:
                    el.append(Paragraph("• " + escape(txt), BODY))
    else:
        el.append(Paragraph("Aucune observation.", SMALL))

    # Photos
    el.append(Paragraph("Photos", H2))
    if photos:
        for p in photos:
            flow = []
            try:
                data = r2_service.get_bytes(p["url_r2"])
                iw, ih = ImageReader(io.BytesIO(data)).getSize()
                sc = min((9 * cm) / iw, (9 * cm) / ih, 1.0)
                flow.append(RLImage(io.BytesIO(data), width=iw * sc, height=ih * sc))
            except Exception:
                flow.append(Paragraph("[Image non rendable]", SMALL))
            if p.get("legende"):
                flow.append(Paragraph(escape(p["legende"]), BODY))
            meta = []
            if p.get("prise_le"):
                meta.append("Pris le " + p["prise_le"].strftime("%Y-%m-%d %H:%M"))
            if p.get("lat") is not None and p.get("lng") is not None:
                meta.append("Geo %.5f, %.5f" % (p["lat"], p["lng"]))
            if meta:
                flow.append(Paragraph(" · ".join(meta), SMALL))
            flow.append(Spacer(1, 10))
            el.append(KeepTogether(flow))
    else:
        el.append(Paragraph("Aucune photo.", SMALL))

    # Checklist
    coches = sum(1 for c in checklist if c["coche"])
    el.append(Paragraph("Checklist (%d/%d)" % (coches, len(checklist)), H2))
    if checklist:
        for c in checklist:
            mark = "[X]" if c["coche"] else "[-]"
            el.append(Paragraph("%s %s" % (mark, escape(c["label"])), BODY))
            if c["commentaire"]:
                el.append(Paragraph("&nbsp;&nbsp;&nbsp;&nbsp;&#8627; " + escape(c["commentaire"]), SMALL))
    else:
        el.append(Paragraph("Aucun item.", SMALL))

    doc.build(el, onFirstPage=draw_adflo_footer, onLaterPages=draw_adflo_footer)
    return buf.getvalue()


def _fetch_report_data(cur, visite_id, org):
    """Charge toutes les données d'UNE visite (org-scopée). None si introuvable.
    Partagé par le rapport.pdf ET le dépôt GED."""
    cur.execute(
        """SELECT v.id, v.central_project_id, v.profil, v.date_visite, v.statut, v.meteo,
                  v.nom_responsable, v.poste_responsable, u.nom AS auteur_nom
           FROM ad_vis.visites v LEFT JOIN ad_vis.users u ON u.id = v.auteur_user_id
           WHERE v.id = %s AND v.organization_id = %s AND v.deleted_at IS NULL""",
        (visite_id, org),
    )
    vis = cur.fetchone()
    if not vis:
        return None
    cur.execute("SELECT texte FROM ad_vis.notes WHERE visite_id = %s AND deleted_at IS NULL ORDER BY ordre, id", (visite_id,))
    notes = cur.fetchall()
    cur.execute("SELECT type, texte FROM ad_vis.observations WHERE visite_id = %s AND deleted_at IS NULL ORDER BY ordre, id", (visite_id,))
    obs = cur.fetchall()
    cur.execute("SELECT url_r2, legende, lat, lng, prise_le FROM ad_vis.photos WHERE visite_id = %s AND deleted_at IS NULL ORDER BY ordre, id", (visite_id,))
    photos = cur.fetchall()
    cur.execute(
        """SELECT ci.label, COALESCE(cr.coche, FALSE) AS coche, COALESCE(cr.commentaire, '') AS commentaire
           FROM ad_vis.checklist_items ci
           LEFT JOIN ad_vis.checklist_reponses cr ON cr.checklist_item_id = ci.id AND cr.visite_id = %s
           WHERE ci.active = TRUE AND (ci.organization_id IS NULL OR ci.organization_id = %s)
           ORDER BY ci.is_system DESC, ci.ordre, ci.id""",
        (visite_id, org),
    )
    checklist = cur.fetchall()
    return vis, notes, obs, photos, checklist


def _resolve_project(jwt_token, central_project_id):
    """Projet HUB correspondant au central_project_id (dans l'org du JWT), ou None."""
    try:
        for p in hub_service.list_org_projects(jwt_token):
            if str(p.get("id")) == str(central_project_id):
                return p
    except Exception:
        return None
    return None


def register_report_routes(get_conn, jwt_user):
    router = APIRouter()

    def _load(visite_id, org):
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            try:
                return _fetch_report_data(cur, visite_id, org)
            finally:
                cur.close()
        finally:
            conn.close()

    @router.get("/api/visites/{visite_id}/rapport.pdf")
    def rapport_pdf(visite_id: int, user=Depends(jwt_user)):
        data = _load(visite_id, user["organization_id"])
        if data is None:
            raise HTTPException(status_code=404, detail="Visite introuvable")
        vis, notes, obs, photos, checklist = data
        # Nom du projet : best-effort (le rapport téléchargeable tolère le fallback).
        proj = _resolve_project(user["_jwt"], vis["central_project_id"])
        projet_nom = (proj or {}).get("name") or f"Projet #{vis['central_project_id']}"
        pdf = _build_pdf(vis, projet_nom, notes, obs, photos, checklist)
        filename = f"visite-{visite_id}-{vis['date_visite']}.pdf"
        return StreamingResponse(
            io.BytesIO(pdf),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ── Infos pré-dépôt (le front décide bouton/avertissement) ───────────────
    @router.get("/api/visites/{visite_id}/rapport/ged-info")
    def ged_info(visite_id: int, user=Depends(jwt_user)):
        data = _load(visite_id, user["organization_id"])
        if data is None:
            raise HTTPException(status_code=404, detail="Visite introuvable")
        vis = data[0]
        proj = _resolve_project(user["_jwt"], vis["central_project_id"])
        existing = 0
        if proj is not None:
            try:
                existing = hub_service.count_category_docs(user["_jwt"], vis["central_project_id"], VISITE_CHANTIER_CAT, PHASE)
            except Exception:
                existing = 0
        return {
            "finalise": vis["statut"] == "finalise",
            "project_resolved": proj is not None,
            "projet_nom": (proj or {}).get("name"),
            "existing_count": existing,
            "next_version": f"V{existing + 1}",
        }

    # ── Dépôt du rapport dans la GED HUB « Visite de chantier » ──────────────
    @router.post("/api/visites/{visite_id}/rapport/deposer-ged")
    def deposer_ged(visite_id: int, user=Depends(jwt_user)):
        org = user["organization_id"]
        jwt = user["_jwt"]
        data = _load(visite_id, org)
        if data is None:
            raise HTTPException(status_code=404, detail="Visite introuvable")
        vis, notes, obs, photos, checklist = data

        # GARDE 1 : visite finalisée uniquement.
        if vis["statut"] != "finalise":
            raise HTTPException(status_code=400, detail="Le dépôt en GED n'est possible que sur une visite finalisée.")

        # GARDE 2 : projet résolu (nom figé + existence/org confirmées). Jamais « Projet #id ».
        proj = _resolve_project(jwt, vis["central_project_id"])
        if proj is None:
            raise HTTPException(status_code=422, detail="Projet lié introuvable dans Ad HUB — dépôt impossible.")
        projet_nom = proj.get("name") or ""
        if not projet_nom.strip():
            raise HTTPException(status_code=422, detail="Projet lié sans nom dans Ad HUB — dépôt impossible.")

        # Catégorie GED cible de l'org.
        cat_id = hub_service.resolve_category_id(jwt, VISITE_CHANTIER_CAT, PHASE)
        if not cat_id:
            raise HTTPException(status_code=422, detail="Dossier GED « Visite de chantier » introuvable pour cette organisation.")

        # PDF + intégrité.
        pdf = _build_pdf(vis, projet_nom, notes, obs, photos, checklist)
        sha = hashlib.sha256(pdf).hexdigest()
        size = len(pdf)

        # Versioning : compte les dépôts existants -> V<n+1> ; nom horodaté.
        existing = hub_service.count_category_docs(jwt, vis["central_project_id"], VISITE_CHANTIER_CAT, PHASE)
        version_label = f"V{existing + 1}"
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %Hh%M")
        filename = f"Rapport de visite {stamp} ({version_label}).pdf"

        # init -> PUT bytes -> confirm (JWT user forwardé). Cleanup-on-failure.
        try:
            init = hub_service.init_project_document(jwt, vis["central_project_id"], {
                "filename": filename, "mime_type": "application/pdf", "size_bytes": size,
                "category_id": cat_id, "version_label": version_label,
                "description": f"Rapport de visite de chantier Ad VIS (visite #{visite_id})",
                "document_date": str(vis["date_visite"]),
            })
        except hub_service.HubServiceError as e:
            if e.status_code == 403:
                raise HTTPException(status_code=403, detail="Le dépôt en GED requiert un rôle gestionnaire ou responsable du projet.")
            raise HTTPException(status_code=502, detail=f"Ad HUB (init dépôt) : {e.detail}")

        doc_id = init.get("document_id")
        upload_url = init.get("upload_url")
        r2_key = init.get("r2_key")

        try:
            hub_service.put_presigned(upload_url, pdf, "application/pdf")
        except Exception as e:
            _cleanup(jwt, doc_id)
            raise HTTPException(status_code=502, detail="Échec de l'envoi du fichier (dépôt annulé, aucun document conservé).") from e

        try:
            hub_service.confirm_document(jwt, doc_id, sha)
        except Exception as e:
            _cleanup(jwt, doc_id)
            raise HTTPException(status_code=502, detail="Échec de la finalisation du dépôt (annulé, aucun document conservé).") from e

        return {
            "deposited": True, "document_id": doc_id, "r2_key": r2_key,
            "category": VISITE_CHANTIER_CAT, "version_label": version_label,
            "display_name": filename, "projet_nom": projet_nom,
        }

    return router


def _cleanup(jwt, doc_id):
    """Best-effort : soft-delete le doc 'uploading' resté après un échec PUT/confirm."""
    if not doc_id:
        return
    try:
        hub_service.delete_document(jwt, doc_id)
    except Exception:
        pass
