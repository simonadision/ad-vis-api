-- ════════════════════════════════════════════════════════════════════
-- Ad VIS — Migration 003 (Sprint 1, Brique 1) : table notes + soft-delete
--
-- ÉTAT RÉEL constaté avant code (par exécution sur la BD prod) :
--   • aucune table `notes` n'existait (seule `observations`, typée) ;
--   • aucune table n'avait de colonne soft-delete.
-- Cette migration corrige les deux, idempotente (rejouée à chaque boot).
-- ════════════════════════════════════════════════════════════════════

-- Notes texte libre (sans type), rattachées à une visite.
CREATE TABLE IF NOT EXISTS ad_vis.notes (
  id          SERIAL PRIMARY KEY,
  visite_id   INTEGER NOT NULL REFERENCES ad_vis.visites(id) ON DELETE CASCADE,
  texte       TEXT NOT NULL DEFAULT '',
  ordre       INTEGER NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ,
  deleted_at  TIMESTAMPTZ          -- soft-delete réversible (NULL = active)
);
CREATE INDEX IF NOT EXISTS idx_vis_notes_visite_live
  ON ad_vis.notes (visite_id, ordre) WHERE deleted_at IS NULL;

-- Soft-delete + horodatage d'édition sur observations (créée au Sprint 0 sans).
ALTER TABLE ad_vis.observations ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
ALTER TABLE ad_vis.observations ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_vis_obs_visite_live
  ON ad_vis.observations (visite_id, ordre) WHERE deleted_at IS NULL;
