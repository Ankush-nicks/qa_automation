-- 3_manager_connects.csv : every manager connect slot, full history up to today (IST)
-- Used by: connects by day, done / missed / rescheduled / not updated, usual completion,
--          last completed connect per instructor ("never" vs "22 Aug"), bottom quarter not connected.
--
-- status: Completed / Missed / Rescheduled / Reschedule Required / Scheduled.
--   "Scheduled" on a past date = status not updated yet. Missed = no call captured by the tool.
-- connect_qa_score is the QA score OF THE CONNECT CALL (evaluation_type CONNECT), not teaching QA.
-- manager_in_directory = FALSE for manager ids that are not in the managers directory.
WITH mgr_dir AS (
  SELECT LOWER(REPLACE(TRIM(instructormanager_id), '-', '')) AS mid,
         ANY_VALUE(instructor_manager)          AS manager_name,
         ANY_VALUE(instructor_manager_category) AS manager_category
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_instructor_managers_and_instructors_details`
  WHERE instructormanager_id IS NOT NULL
  GROUP BY 1
),
pair_dir AS (
  SELECT DISTINCT LOWER(REPLACE(TRIM(instructormanager_id), '-', '')) AS mid,
                  LOWER(REPLACE(TRIM(instructor_user_id), '-', ''))   AS iid
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_instructor_managers_and_instructors_details`
),
cq AS (
  SELECT evaluation_id, ANY_VALUE(status) AS connect_qa_status, ANY_VALUE(final_score) AS final_score
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_session_wise_qa_rating`
  WHERE UPPER(TRIM(evaluation_type)) = 'CONNECT'
  GROUP BY evaluation_id
),
c AS (
  SELECT c.*,
         LOWER(REPLACE(TRIM(c.instructor_manager_user_id), '-', '')) AS mid,
         LOWER(REPLACE(TRIM(c.instructor_user_id), '-', ''))         AS iid
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_manager_connect_session_details` c
)
SELECT
  c.session_id                      AS connect_id,
  c.scheduled_date,
  c.week_start_date,
  c.scheduled_start_time,
  c.scheduled_end_time,
  c.duration_minutes,
  c.status,
  c.transcript_status,
  c.summary_status,
  c.mid                             AS manager_id,
  md.manager_name,
  md.manager_category,
  md.mid IS NOT NULL                AS manager_in_directory,
  pd.iid IS NOT NULL                AS pair_in_directory,
  c.iid                             AS instructor_user_id,
  CAST(c.evaluation_id AS STRING)   AS connect_evaluation_id,
  cq.connect_qa_status,
  IF(LOWER(cq.connect_qa_status) = 'completed', cq.final_score, NULL) AS connect_qa_score
FROM c
LEFT JOIN mgr_dir md  ON md.mid = c.mid
LEFT JOIN pair_dir pd ON pd.mid = c.mid AND pd.iid = c.iid
LEFT JOIN cq          ON cq.evaluation_id = CAST(c.evaluation_id AS STRING)
WHERE c.scheduled_date <= CURRENT_DATE('Asia/Kolkata')
ORDER BY c.scheduled_date, md.manager_name, c.scheduled_start_time;
