-- ════════════════════════════════════════════════════════════════════
-- Ad VIS — Migration 007 : notes de calendrier par PROJET
--
-- Notes datées LIBRES rattachées à un PROJET HUB (central_project_id),
-- INDÉPENDANTES des visites : l'utilisateur clique une date du calendrier
-- mensuel et ajoute une ou plusieurs notes (texte, dictée vocale).
-- Scopée organization_id (TOUJOURS forcé depuis le JWT, jamais du client) —
-- même modèle d'isolation que ad_vis.visites. auteur_user_id : convention
-- identique aux visites (FK ad_vis.users, nullable). Soft-delete réversible
-- (deleted_at), jamais de hard-delete.
-- Idempotente (CREATE … IF NOT EXISTS), rejouée à chaque boot par _bootstrap_db().
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS ad_vis.notes_calendrier (
  id                 SERIAL PRIMARY KEY,
  central_project_id BIGINT NOT NULL,
  organization_id    UUID NOT NULL,
  date_note          DATE NOT NULL,
  texte              TEXT NOT NULL,
  auteur_user_id     INTEGER REFERENCES ad_vis.users(id),
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at         TIMESTAMPTZ
);

-- Liste par projet + filtre mois (org en tête = cloisonnement).
CREATE INDEX IF NOT EXISTS idx_vis_notescal_project_date
  ON ad_vis.notes_calendrier (organization_id, central_project_id, date_note);
