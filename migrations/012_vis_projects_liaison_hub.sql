-- ═══════════════════════════════════════════════════════════════════════════
-- Ad VIS — Migration 012 : table de liaison projet (opt-in Ad HUB)
--
-- Simon 2026-08-14 : « le code ouvre les projets systématiquement dans tous
-- les modules et ce n'est pas la commande exigée. Si j'ouvre un projet dans
-- Ad HUB, 1 chance sur 10 que ce projet soit réalisé. Donc 9/10 des chances
-- que ce projet soit juste en budget ou soumission. Alors chaque module doit
-- avoir son bouton nouveau projet avec une modale qui nous permet d'aller
-- chercher un projet depuis Ad HUB. »
--
-- Ad VIS n'avait AUCUNE table de projets : son écran de sélection était un
-- proxy direct sur GET {hub}/api/projects, donc tous les projets de l'org
-- s'y affichaient. Le correctif du 2026-08-13 (drapeau modules_actifs.ad_vis)
-- n'était qu'un opt-OUT : il fallait retirer les projets un par un. C'est
-- l'inverse du besoin — et c'est exactement ce geste de « nettoyage » qui a
-- détruit six projets de production via Ad VIS le 2026-08-13.
--
-- Cette table matérialise l'appartenance : un projet est dans Ad VIS parce
-- que quelqu'un l'y a amené, point.
--
-- Ce qu'elle N'EST PAS :
--   - une copie du projet : l'identité reste dans app_hub.projects. Les
--     colonnes name/code/client_name sont un CACHE d'affichage, pour que
--     l'écran de sélection reste lisible quand le hub ne répond pas.
--   - un prérequis aux visites : ad_vis.visites.central_project_id continue
--     de porter l'id hub directement. Aucune FK entre les deux, donc délier
--     un projet ne met aucune visite, photo ou observation en danger.
--
-- Suppression = soft-delete. Délier puis relier ne doit pas perdre la trace
-- de qui avait lié quoi.
--
-- Idempotente (CREATE … IF NOT EXISTS), rejouée à chaque boot Railway par
-- _bootstrap_db() / ad_vis.schema_migrations.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS ad_vis.vis_projects (
  id                 BIGSERIAL PRIMARY KEY,
  -- UUID comme partout dans ad_vis (cf. ad_vis.visites.organization_id).
  organization_id    UUID        NOT NULL,
  -- id du projet dans app_hub.projects. FK CONCEPTUELLE : bases séparées,
  -- pas de contrainte dure possible.
  central_project_id INTEGER     NOT NULL,
  -- Cache d'affichage (voir en-tête) — jamais la source de vérité.
  name               TEXT        NULL,
  code               TEXT        NULL,
  client_name        TEXT        NULL,
  linked_by          TEXT        NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at         TIMESTAMPTZ NULL
);

-- Un projet ne peut être lié qu'une fois par organisation. Index PARTIEL sur
-- deleted_at IS NULL : après un retrait, on doit pouvoir relier.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_vis_projects_org_central
  ON ad_vis.vis_projects (organization_id, central_project_id)
  WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_vis_projects_org_actifs
  ON ad_vis.vis_projects (organization_id)
  WHERE deleted_at IS NULL;

COMMENT ON TABLE ad_vis.vis_projects IS
  'Projets Ad HUB explicitement liés à Ad VIS (opt-in, Simon 2026-08-14). '
  'L''écran de sélection lit CETTE table, plus la liste complète du hub. '
  'name/code/client_name = cache d''affichage, jamais la source de vérité : '
  'l''identité du projet vit dans app_hub.projects.';

COMMENT ON COLUMN ad_vis.vis_projects.central_project_id IS
  'app_hub.projects.id — FK conceptuelle (bases séparées). Aucune FK vers '
  'ad_vis.visites.central_project_id : délier un projet ne touche aucune visite.';

COMMENT ON COLUMN ad_vis.vis_projects.deleted_at IS
  'Soft-delete du LIEN, jamais du projet. Retirer un projet d''Ad VIS ne doit '
  'rien écrire dans Ad HUB — c''est le piège qui a coûté six projets le 2026-08-13.';

COMMENT ON COLUMN ad_vis.vis_projects.linked_by IS
  'Courriel de la personne qui a amené le projet dans Ad VIS. Conservé même '
  'après un déliage : on veut pouvoir retracer qui avait ouvert quoi.';
