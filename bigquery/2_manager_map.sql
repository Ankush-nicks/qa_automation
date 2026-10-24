-- 2_manager_map.csv : instructor <-> manager, one row per pair (instructors without a manager kept once)
-- Used by: assigning each instructor to a Capability Manager, manager portfolio size, names and emails.
--
-- in_manager_ids  = the manager id is recorded on the instructor's session data (instructor_manager_ids)
-- in_directory    = the pair is in the managers-and-instructors directory
-- is_org_wide     = the manager covers 300+ instructors (head, not a Capability Manager)
-- is_capability_manager_pick / pick_source = the same rule the QA email app uses today
--   DIRECT: only one id recorded; DIRECT_RESOLVED: several recorded, kept the same-category one(s);
--   INFERRED_FROM_MANAGER_VIEW: none recorded, the same-category non-org-wide manager in the directory.
WITH roster AS (
  SELECT LOWER(REPLACE(TRIM(instructor_user_id), '-', '')) AS iid,
         instructor_user_id AS raw_iid, nw_instructor_id, instructor_name, instructor_category,
         instructor_role, instructor_status, institute_id, institute_name, institute_type
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_instructor_details`
),

user_emails AS (
  SELECT LOWER(REPLACE(TRIM(instructor_user_id), '-', '')) AS uid, ANY_VALUE(LOWER(TRIM(instructor_mail))) AS email
  FROM (
    SELECT instructor_user_id, instructor_mail FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_session_wise_qa_rating`
    UNION ALL
    SELECT instructor_user_id, instructor_mail FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_session_wise_rubric_metrics_details`
    UNION ALL
    SELECT instructor_user_id, instructor_mail FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_instructor_wise_attendance_details_transformed`
    UNION ALL
    SELECT instructor_user_id, instructor_mail FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_session_wise_user_feedback_details_with_sentiment_type`
  )
  WHERE instructor_mail IS NOT NULL AND TRIM(instructor_mail) != ''
  GROUP BY 1
),

mgr_view AS (
  SELECT DISTINCT
    LOWER(REPLACE(TRIM(instructor_user_id), '-', ''))    AS iid,
    LOWER(REPLACE(TRIM(instructormanager_id), '-', ''))  AS mid,
    instructor_manager, instructor_manager_category
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_instructor_managers_and_instructors_details`
  WHERE instructormanager_id IS NOT NULL
),

mgr_dir AS (
  SELECT mid,
         ANY_VALUE(instructor_manager)          AS manager_name,
         ANY_VALUE(instructor_manager_category) AS manager_category,
         COUNT(DISTINCT iid)                    AS portfolio_size
  FROM mgr_view
  GROUP BY mid
),

direct_mgr AS (
  SELECT DISTINCT LOWER(REPLACE(TRIM(instructor_user_id), '-', '')) AS iid,
                  LOWER(REPLACE(TRIM(m), '-', ''))                  AS mid
  FROM (
    SELECT instructor_user_id, instructor_manager_ids FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_session_wise_rubric_metrics_details`
    UNION ALL
    SELECT instructor_user_id, instructor_manager_ids FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_sessions_wise_summary_details`
    UNION ALL
    SELECT instructor_user_id, instructor_manager_ids FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_session_wise_user_feedback_details_with_sentiment_type`
  ), UNNEST(SPLIT(instructor_manager_ids, ',')) AS m
  WHERE instructor_manager_ids IS NOT NULL AND TRIM(m) != ''
),

-- the app's capability-manager pick
picks AS (
  SELECT d.iid, d.mid,
         IF(COUNT(*) OVER (PARTITION BY d.iid) = 1, 'DIRECT', 'DIRECT_RESOLVED') AS pick_source
  FROM direct_mgr d
  JOIN roster r USING (iid)
  LEFT JOIN mgr_dir md USING (mid)
  WHERE TRUE
  QUALIFY COUNT(*) OVER (PARTITION BY d.iid) = 1
       OR md.manager_category = r.instructor_category
  UNION ALL
  SELECT r.iid, mv.mid, 'INFERRED_FROM_MANAGER_VIEW'
  FROM roster r
  JOIN mgr_view mv ON mv.iid = r.iid AND mv.instructor_manager_category = r.instructor_category
  JOIN mgr_dir md  ON md.mid = mv.mid AND md.portfolio_size < 300
  WHERE r.iid NOT IN (SELECT iid FROM direct_mgr)
),

pairs AS (
  SELECT iid, mid,
         LOGICAL_OR(src = 'IDS') AS in_manager_ids,
         LOGICAL_OR(src = 'DIR') AS in_directory
  FROM (SELECT iid, mid, 'IDS' AS src FROM direct_mgr
        UNION ALL
        SELECT iid, mid, 'DIR' FROM mgr_view)
  GROUP BY iid, mid
)

SELECT
  r.iid                                  AS instructor_user_id,
  r.nw_instructor_id,
  r.instructor_name,
  ie.email                               AS instructor_email,
  r.instructor_category,
  r.instructor_role,
  r.instructor_status,
  r.institute_id                         AS home_campus_id,
  r.institute_name                       AS home_campus,
  r.institute_type                       AS home_campus_type,
  p.mid                                  AS manager_id,
  md.manager_name,
  md.manager_category,
  me.email                               AS manager_email,
  md.portfolio_size                      AS manager_portfolio_size,
  IFNULL(md.portfolio_size >= 300, FALSE) AS is_org_wide,
  IFNULL(p.in_manager_ids, FALSE)        AS in_manager_ids,
  IFNULL(p.in_directory, FALSE)          AS in_directory,
  pk.mid IS NOT NULL                     AS is_capability_manager_pick,
  pk.pick_source
FROM roster r
LEFT JOIN pairs p        ON p.iid = r.iid
LEFT JOIN mgr_dir md     ON md.mid = p.mid
LEFT JOIN user_emails me ON me.uid = p.mid
LEFT JOIN user_emails ie ON ie.uid = r.iid
LEFT JOIN (SELECT DISTINCT iid, mid, pick_source FROM picks) pk ON pk.iid = r.iid AND pk.mid = p.mid
ORDER BY r.instructor_name, md.manager_name;
