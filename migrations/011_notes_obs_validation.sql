-- ═══════════════════════════════════════════════════════════════════════════
-- Ad VIS — Migration 011 : VALIDATION d'une note / observation
--
-- Besoin terrain (Simon) : après avoir saisi une note, l'inspecteur doit
-- pouvoir la MARQUER COMME VALIDÉE avant de passer à la suivante. Sans ce
-- statut, rien ne distingue une note relue d'une note tout juste dictée —
-- l'écran n'offrait que « Modifier » et « Supprimer ».
--
-- Choix de modélisation : un HORODATAGE nullable plutôt qu'un booléen.
--   · NULL      = non validée
--   · non NULL  = validée, et l'on sait QUAND
-- Un booléen aurait perdu cette information, précieuse pour un rapport de
-- visite qui fait foi. Même parti pris que `deleted_at` (migration 003/008).
--
-- Idempotente : ADD COLUMN IF NOT EXISTS. Rejouée à chaque boot de l'API.
-- Rétrocompatible : colonne NULLABLE, aucune valeur par défaut — les notes
-- existantes restent simplement « non validées », rien n'est réécrit.
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE ad_vis.notes
  ADD COLUMN IF NOT EXISTS valide_at TIMESTAMPTZ NULL;

ALTER TABLE ad_vis.observations
  ADD COLUMN IF NOT EXISTS valide_at TIMESTAMPTZ NULL;

-- Index partiels : les écrans filtrent « ce qui reste à valider » sur une
-- visite. Partiels car seules les lignes NON validées sont interrogées ainsi.
CREATE INDEX IF NOT EXISTS notes_a_valider_idx
  ON ad_vis.notes (visite_id)
  WHERE valide_at IS NULL AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS observations_a_valider_idx
  ON ad_vis.observations (visite_id)
  WHERE valide_at IS NULL AND deleted_at IS NULL;
