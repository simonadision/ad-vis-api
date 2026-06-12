-- ════════════════════════════════════════════════════════════════════
-- Ad VIS — Migration 010 : type d'observation « manutention » + champs structurés
--
-- Note de RÉFÉRENCE pour le chiffrage (Ad FLO ne calcule/multiplie RIEN —
-- temps_estime_min est une note, pas une variable de formule). N'affecte PAS
-- les 5 types existants : nouvelles colonnes NULLABLES, remplies uniquement
-- pour type='manutention'. La description réutilise le champ `texte`.
--
-- Idempotente : DROP+ADD du CHECK (étend la liste autorisée — même pattern que
-- la fusion checklist 006) ; ADD COLUMN IF NOT EXISTS. Rejouée à chaque boot.
-- ════════════════════════════════════════════════════════════════════

ALTER TABLE ad_vis.observations DROP CONSTRAINT IF EXISTS observations_type_check;
ALTER TABLE ad_vis.observations ADD CONSTRAINT observations_type_check
  CHECK (type IN ('acces', 'contrainte', 'etat_existant', 'question', 'constat', 'manutention'));

ALTER TABLE ad_vis.observations ADD COLUMN IF NOT EXISTS operation        TEXT;
ALTER TABLE ad_vis.observations ADD COLUMN IF NOT EXISTS trajet           TEXT;
ALTER TABLE ad_vis.observations ADD COLUMN IF NOT EXISTS temps_estime_min INTEGER;
