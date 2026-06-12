-- ════════════════════════════════════════════════════════════════════
-- Ad VIS — Migration 006 : checklist COMMUNE (fusion estimateur+contremaitre)
--
-- NON DESTRUCTIF : les 10 anciens items système sont DÉSACTIVÉS (active=false),
-- jamais supprimés — les checklist_reponses existantes (FK ON DELETE CASCADE)
-- sont ainsi PRÉSERVÉES (orphelines mais intactes). 8 items communs insérés
-- (profil neutre 'commun' ; GET ne filtre plus par profil). Idempotente.
-- ════════════════════════════════════════════════════════════════════

-- 1) Autoriser le profil neutre 'commun' sur les items (idempotent : drop+add).
--    NB : ad_vis.visites.profil garde son CHECK estimateur|contremaitre (inchangé).
ALTER TABLE ad_vis.checklist_items DROP CONSTRAINT IF EXISTS checklist_items_profil_check;
ALTER TABLE ad_vis.checklist_items ADD CONSTRAINT checklist_items_profil_check
  CHECK (profil = ANY (ARRAY['estimateur'::text, 'contremaitre'::text, 'commun'::text]));

-- 2) Insérer les 8 items communs (idempotent NOT EXISTS, ordre 10→80).
INSERT INTO ad_vis.checklist_items (organization_id, profil, label, ordre, is_system, active)
SELECT NULL::uuid, 'commun', s.label, s.ordre, TRUE, TRUE
FROM (VALUES
  ('Sécurité du chantier (CNESST)',                          10),
  ('Accès au site (camion, livraison, stationnement)',       20),
  ('État des lieux / conditions existantes (démolition ?)',  30),
  ('Contraintes d''espace / hauteur libre',                  40),
  ('Services existants (élec, plomberie, gaz)',              50),
  ('Branchements temporaires disponibles',                   60),
  ('Coordination avec autres corps de métier',               70),
  ('Photos d''ensemble prises',                              80)
) AS s(label, ordre)
WHERE NOT EXISTS (
  SELECT 1 FROM ad_vis.checklist_items c
  WHERE c.is_system = TRUE AND c.organization_id IS NULL
    AND c.profil = 'commun' AND c.label = s.label
);

-- 3) Désactiver les anciens items système (estimateur/contremaitre) — préserve
--    leurs réponses. Rejouable (déjà inactifs au 2e boot -> no-op).
UPDATE ad_vis.checklist_items
   SET active = FALSE
 WHERE is_system = TRUE AND organization_id IS NULL AND profil <> 'commun' AND active = TRUE;
