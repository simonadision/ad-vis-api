-- ════════════════════════════════════════════════════════════════════
-- Ad VIS — Migration 009 : annotation de photos (original CONSERVÉ)
--
-- La version annotée (aplatie) d'une photo = NOUVELLE ligne ad_vis.photos
-- liée à l'originale par annotated_from_photo_id (nullable, FK). L'ORIGINAL
-- n'est JAMAIS écrasé ni supprimé : il garde sa ligne ET son objet R2.
-- Règle d'affichage (galerie + PDF) : on montre la « feuille » = photo non
-- supprimée SANS enfant annoté vivant → l'annotée remplace l'originale à
-- l'affichage, les deux coexistent en R2 (réversible : supprimer l'annotée
-- fait réapparaître l'originale). Idempotente (IF NOT EXISTS), rejouée au boot.
-- ════════════════════════════════════════════════════════════════════

ALTER TABLE ad_vis.photos
  ADD COLUMN IF NOT EXISTS annotated_from_photo_id INTEGER REFERENCES ad_vis.photos(id);

CREATE INDEX IF NOT EXISTS idx_vis_photos_annotated_from
  ON ad_vis.photos (annotated_from_photo_id)
  WHERE annotated_from_photo_id IS NOT NULL;
