-- Canary-safe views (PRD §6.3, §7). Fact queries read only these; missing and canary repos never count.
CREATE OR REPLACE VIEW v_repos AS
    SELECT * FROM _base.repos WHERE NOT coalesce(missing, false) AND NOT coalesce(canary, false);
CREATE OR REPLACE VIEW v_artifacts AS
    SELECT a.* FROM _base.artifacts a JOIN v_repos r USING (repo);
CREATE OR REPLACE VIEW v_membership AS
    SELECT m.* FROM _base.membership m JOIN v_artifacts a USING (artifact_id);
CREATE OR REPLACE VIEW v_clusters AS
    SELECT c.* FROM _base.clusters c WHERE c.cluster_id IN (SELECT cluster_id FROM v_membership);
CREATE OR REPLACE VIEW v_features AS
    SELECT f.* FROM _base.features f JOIN v_repos r USING (repo);
CREATE OR REPLACE VIEW v_glyphs AS
    SELECT g.* FROM _base.glyphs g JOIN v_repos r USING (repo);
CREATE OR REPLACE VIEW v_semantics AS
    SELECT s.* FROM _base.semantics s WHERE s.pass_id = 'a' AND s.cluster_id IN (SELECT cluster_id FROM v_clusters);
CREATE OR REPLACE VIEW v_use_cases AS
    SELECT * FROM _base.use_cases;
CREATE OR REPLACE VIEW v_uc_leaf_repos AS
    SELECT DISTINCT u.use_case_id, a.repo
    FROM _base.uc_membership u JOIN v_membership m USING (cluster_id) JOIN v_artifacts a USING (artifact_id);
CREATE OR REPLACE VIEW v_uc_repos AS
    SELECT use_case_id, repo FROM v_uc_leaf_repos
    UNION
    SELECT uc.parent_id AS use_case_id, l.repo
    FROM v_uc_leaf_repos l JOIN _base.use_cases uc USING (use_case_id)
    WHERE uc.parent_id IS NOT NULL;
