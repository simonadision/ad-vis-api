-- ════════════════════════════════════════════════════════════════════
-- Ad VIS — Migration 004 (Sprint 1, Brique 2) : soft-delete sur photos
--
-- ad_vis.photos existait depuis la création initiale (6 tables) MAIS sans
-- colonne soft-delete. On aligne sur notes/observations (Brique 1) :
-- deleted_at + updated_at + index partiel « live ». Idempotente.
-- ════════════════════════════════════════════════════════════════════

ALTER TABLE ad_vis.photos ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
ALTER TABLE ad_vis.photos ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_vis_photos_visite_live
  ON ad_vis.photos (visite_id, ordre) WHERE deleted_at IS NULL;
