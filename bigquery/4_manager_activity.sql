-- 4_manager_activity.csv : platform events per manager per day
-- Used by: "Platform use" in the manager table (active days, connect-tab clicks, QA reports opened).
--
-- TEMPLATE. run_exports.py reads the event view's columns from INFORMATION_SCHEMA and fills in
-- {USER_COL}, {EVENT_COL} and {DAY_EXPR}, then saves the filled query as 4_manager_activity.filled.sql.
-- To run it by hand in the BigQuery console, replace the three placeholders yourself, e.g.
--   {USER_COL} -> user_id    {EVENT_COL} -> event_name    {DAY_EXPR} -> DATE(event_timestamp, 'Asia/Kolkata')
--
-- Window: same as 1_qa_daily (28 days before the 1st of the reporting month) up to today.
-- Only users who are managers in the managers directory are kept.
WITH params AS (
  SELECT
    DATE_SUB(DATE_TRUNC(DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 1 DAY), MONTH), INTERVAL 28 DAY) AS start_date,
    CURRENT_DATE('Asia/Kolkata') AS end_date
),
mgr AS (
  SELECT LOWER(REPLACE(TRIM(instructormanager_id), '-', '')) AS mid,
         ANY_VALUE(instructor_manager) AS manager_name
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_instructor_managers_and_instructors_details`
  WHERE instructormanager_id IS NOT NULL
  GROUP BY 1
),
ev AS (
  SELECT {DAY_EXPR}                                              AS d,
         LOWER(REPLACE(TRIM(CAST({USER_COL} AS STRING)), '-', '')) AS uid,
         CAST({EVENT_COL} AS STRING)                             AS event_name
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_instructor_platform_cloud_watch_events_data`
)
SELECT ev.d, ev.uid AS manager_id, mgr.manager_name, ev.event_name, COUNT(*) AS events
FROM ev
JOIN mgr ON mgr.mid = ev.uid
CROSS JOIN params p
WHERE ev.d BETWEEN p.start_date AND p.end_date
GROUP BY 1, 2, 3, 4
ORDER BY 1, 3, 4;
