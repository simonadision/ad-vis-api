-- ════════════════════════════════════════════════════════════════════
-- Ad VIS — Migration 002 : observations, photos, checklist (+ seed système)
--
-- Tables créées DÈS le Sprint 0 (le modèle complet) ; leurs endpoints CRUD
-- arrivent aux Sprints 1-2. Idempotente. Le seed des checklist_items système
-- (organization_id NULL, is_system) est gardé par NOT EXISTS → rejouable sans
-- doublon à chaque boot.
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS ad_vis.observations (
  id                        SERIAL PRIMARY KEY,
  visite_id                 INTEGER NOT NULL REFERENCES ad_vis.visites(id) ON DELETE CASCADE,
  type                      TEXT NOT NULL CHECK (type IN ('acces', 'contrainte', 'etat_existant', 'question', 'constat')),
  texte                     TEXT NOT NULL DEFAULT '',
  note_vocale_transcription TEXT,
  ordre                     INTEGER NOT NULL DEFAULT 0,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vis_obs_visite ON ad_vis.observations (visite_id, ordre);

CREATE TABLE IF NOT EXISTS ad_vis.photos (
  id             SERIAL PRIMARY KEY,
  visite_id      INTEGER NOT NULL REFERENCES ad_vis.visites(id) ON DELETE CASCADE,
  observation_id INTEGER REFERENCES ad_vis.observations(id) ON DELETE SET NULL,
  url_r2         TEXT NOT NULL,
  legende        TEXT NOT NULL DEFAULT '',
  lat            DOUBLE PRECISION,
  lng            DOUBLE PRECISION,
  prise_le       TIMESTAMPTZ,
  ordre          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_vis_photos_visite ON ad_vis.photos (visite_id, ordre);

-- Items de checklist : système (organization_id NULL, is_system) seedés
-- ci-dessous ; les orgs pourront ajouter les leurs (is_system FALSE) plus tard.
CREATE TABLE IF NOT EXISTS ad_vis.checklist_items (
  id              SERIAL PRIMARY KEY,
  organization_id UUID,
  profil          TEXT NOT NULL CHECK (profil IN ('estimateur', 'contremaitre')),
  label           TEXT NOT NULL,
  ordre           INTEGER NOT NULL DEFAULT 0,
  is_system       BOOLEAN NOT NULL DEFAULT FALSE,
  active          BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_vis_checklist_profil ON ad_vis.checklist_items (profil, ordre);

CREATE TABLE IF NOT EXISTS ad_vis.checklist_reponses (
  id                SERIAL PRIMARY KEY,
  visite_id         INTEGER NOT NULL REFERENCES ad_vis.visites(id) ON DELETE CASCADE,
  checklist_item_id INTEGER NOT NULL REFERENCES ad_vis.checklist_items(id) ON DELETE CASCADE,
  coche             BOOLEAN NOT NULL DEFAULT FALSE,
  commentaire       TEXT NOT NULL DEFAULT '',
  UNIQUE (visite_id, checklist_item_id)
);

-- Seed checklist SYSTÈME (org NULL) — idempotent via NOT EXISTS (pas besoin de
-- contrainte UNIQUE ; le runner rejoue sans doublon).
INSERT INTO ad_vis.checklist_items (organization_id, profil, label, ordre, is_system, active)
SELECT s.org, s.profil, s.label, s.ordre, TRUE, TRUE
FROM (VALUES
  (NULL::uuid, 'estimateur',   'Accès au site (camion, stationnement)',          10),
  (NULL::uuid, 'estimateur',   'Conditions existantes (démolition requise ?)',   20),
  (NULL::uuid, 'estimateur',   'Contraintes d''espace / hauteur libre',          30),
  (NULL::uuid, 'estimateur',   'Services existants (élec, plomberie, gaz)',      40),
  (NULL::uuid, 'estimateur',   'Photos d''ensemble prises',                      50),
  (NULL::uuid, 'contremaitre', 'Sécurité du chantier (CNESST)',                  10),
  (NULL::uuid, 'contremaitre', 'Accès et zone de livraison',                     20),
  (NULL::uuid, 'contremaitre', 'État des lieux avant travaux',                   30),
  (NULL::uuid, 'contremaitre', 'Branchements temporaires disponibles',           40),
  (NULL::uuid, 'contremaitre', 'Coordination avec autres corps de métier',       50)
) AS s(org, profil, label, ordre)
WHERE NOT EXISTS (
  SELECT 1 FROM ad_vis.checklist_items c
  WHERE c.is_system = TRUE AND c.organization_id IS NULL
    AND c.profil = s.profil AND c.label = s.label
);
