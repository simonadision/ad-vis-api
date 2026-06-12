-- ════════════════════════════════════════════════════════════════════
-- Ad VIS — Migration 008 : SOFT-DELETE des visites (réversible)
--
-- Ajoute deleted_at à ad_vis.visites (la racine n'en avait pas — notes/obs/
-- photos l'ont depuis les briques 003/004/002). Ajoute aussi deleted_at à
-- ad_vis.checklist_reponses pour que la SUPPRESSION D'UNE VISITE puisse
-- soft-deleter EN CASCADE tous ses contenus (notes, observations, photos,
-- réponses checklist) de façon RÉVERSIBLE — jamais de hard-delete.
-- Les objets R2 des photos NE SONT PAS purgés ; le HUB/GED n'est pas touché.
-- Idempotente (ADD COLUMN IF NOT EXISTS), rejouée à chaque boot.
-- ════════════════════════════════════════════════════════════════════

ALTER TABLE ad_vis.visites            ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE ad_vis.checklist_reponses ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
