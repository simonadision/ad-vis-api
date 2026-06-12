-- ════════════════════════════════════════════════════════════════════
-- Ad VIS — Migration 005 (Brique A) : responsable de visite (nom + poste)
--
-- Champs TEXTE LIBRE optionnels (nullable), SANS FK : le responsable peut être
-- externe / non-user. profil (estimateur|contremaitre) est CONSERVÉ tel quel
-- (NOT NULL, pilote la checklist/PDF/GED) — le responsable est ADDITIF.
-- Idempotente (ADD COLUMN IF NOT EXISTS), rejouée à chaque boot.
-- ════════════════════════════════════════════════════════════════════

ALTER TABLE ad_vis.visites ADD COLUMN IF NOT EXISTS nom_responsable   TEXT;
ALTER TABLE ad_vis.visites ADD COLUMN IF NOT EXISTS poste_responsable TEXT;
