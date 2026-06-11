-- ════════════════════════════════════════════════════════════════════
-- Ad VIS — Migration 001 : schéma ad_vis + users (SSO) + visites
--
-- Idempotente (CREATE … IF NOT EXISTS) : rejouée à chaque boot Railway par
-- _bootstrap_db(). users = miroir local SSO (auto-provision, cf. auth_jwt).
-- visites = entité racine du Sprint 0 (rattachée à un projet HUB via
-- central_project_id, scopée organization_id).
-- ════════════════════════════════════════════════════════════════════

CREATE SCHEMA IF NOT EXISTS ad_vis;

-- Miroir local des utilisateurs SSO (FK pour auteur_user_id).
CREATE TABLE IF NOT EXISTS ad_vis.users (
  id            SERIAL PRIMARY KEY,
  email         TEXT UNIQUE NOT NULL,
  nom           TEXT NOT NULL DEFAULT '',
  role          TEXT NOT NULL DEFAULT 'user',
  actif         BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_login_at TIMESTAMPTZ
);

-- Visite de chantier (pré-construction). central_project_id = app_hub.projects.id
-- (entier côté HUB). organization_id = UUID de l'org (forcé depuis le JWT).
CREATE TABLE IF NOT EXISTS ad_vis.visites (
  id                 SERIAL PRIMARY KEY,
  central_project_id BIGINT NOT NULL,
  organization_id    UUID NOT NULL,
  profil             TEXT NOT NULL CHECK (profil IN ('estimateur', 'contremaitre')),
  auteur_user_id     INTEGER REFERENCES ad_vis.users(id),
  date_visite        DATE NOT NULL DEFAULT CURRENT_DATE,
  statut             TEXT NOT NULL DEFAULT 'brouillon' CHECK (statut IN ('brouillon', 'finalise')),
  meteo              TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vis_visites_project
  ON ad_vis.visites (organization_id, central_project_id);
CREATE INDEX IF NOT EXISTS idx_vis_visites_statut
  ON ad_vis.visites (statut);
