-- NIAT QA: ONE query for both pages of the app (Instructor emails + University drill-down).
--
-- Output: one table with two columns, `part` and `row_json` (one JSON object per row).
-- Save the whole result as ONE CSV (e.g. niat_all_in_one.csv) and upload it on either page:
-- the app splits it into the files it already reads (01_universities ... 13_tickets_month and
-- instructor_qa_last3_export) and updates both pages.
--
-- The result is about 19-24 MB, above the console's 10 MB "download CSV" limit. Use
-- "Save results > CSV (Google Drive)", or in Cloud Shell:
--   bq query --nouse_legacy_sql --format=csv --max_rows=5000000 < niat_all_in_one.sql > niat_all_in_one.csv
--
-- One set of rules, used by every part (so both pages show the same numbers):
--   * IDs are compared lower-case without hyphens.
--   * QA score of a session = its latest SCHEDULE evaluation that is completed and scored, for a LECTURE or
--     PRACTICE session whose schedule has one session type and one instructor, and the report belongs to that
--     instructor. A 0 with no rubric rows is dropped (failed evaluation). One score per session section.
--   * An instructor's QA days = their last 3 days with a QA score, up to yesterday (IST), looked for within
--     60 days. Instructors are listed for QA only when they have a QA day in the last 14 days.
--   * Below 5 = EACH of the last 3 QA days has a daily average below 5 (all 3 days needed).
--   * Capability manager = the instructor's managers that are on the list of 14 capability managers below.
--   * Weeks start on Monday. run_date = today (IST); the 14-day window is run_date - 13 .. run_date.
--   * 15_qa_days: each instructor's QA per day for the last 60 days up to yesterday (sum and count of the
--     scores above), so the instructor emails page can average any date range.
--   * An instructor's workload counts each session once: a class merged across sections is one session
--     (same session and start time), with its scheduled start-to-end minutes.
-- Edit only the `params` and `cap_list` CTEs. Nothing is written to BigQuery.

WITH
params AS (
  SELECT
    run_date,
    DATE_SUB(run_date, INTERVAL 13 DAY)                                AS from_date,
    DATE_ADD(run_date, INTERVAL 1 DAY)                                 AS next_day,
    DATE_TRUNC(run_date, WEEK(MONDAY))                                 AS week_start,
    DATE_SUB(DATE_TRUNC(run_date, WEEK(MONDAY)), INTERVAL 7 WEEK)      AS from_8wk,
    DATE_TRUNC(run_date, MONTH)                                        AS month_start,
    DATE_SUB(run_date, INTERVAL 1 DAY)                                 AS qa_end,
    DATE_SUB(run_date, INTERVAL 60 DAY)                                AS qa_lookback,
    DATE_SUB(run_date, INTERVAL 14 DAY)                                AS qa_recent,
    5.0                                                                AS threshold
  FROM (SELECT CURRENT_DATE('Asia/Kolkata') AS run_date)
),
cap_list AS (
  SELECT LOWER(TRIM(name)) AS cap
  FROM UNNEST([
    'Riya', 'Kushal Kumar Terli', 'Dharavath Jayanth', 'Solasa Vinay', 'Voppangi Sai Prasanna', 'Sigatapu Sai Sankar',
    'Boddikurapati Yaswanth', 'shaik mohammed pasha', 'Penumarthi Satya Syamala', 'Meka Sri Satya Prudhvi Charan',
    'Katuri Karthik', 'Preethi Vangaveti', 'Akhilendar Reddy Karri', 'Pradeep']) AS name
),

-- =====================================================================  people
roster AS (
  SELECT LOWER(REPLACE(TRIM(instructor_user_id), '-', '')) AS iid,
         ANY_VALUE(instructor_name) AS instructor_name, ANY_VALUE(instructor_category) AS instructor_category,
         ANY_VALUE(instructor_status) AS instructor_status, ANY_VALUE(institute_name) AS institute_name
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_instructor_details`
  WHERE instructor_user_id IS NOT NULL
  GROUP BY iid
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
  GROUP BY uid
),
-- every manager mapped to an instructor
mgr_view AS (
  SELECT DISTINCT LOWER(REPLACE(TRIM(instructor_user_id), '-', ''))   AS iid,
                  LOWER(REPLACE(TRIM(instructormanager_id), '-', '')) AS mid,
                  TRIM(instructor_manager) AS manager, instructor_manager_category AS manager_category,
                  instructor_status
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_instructor_managers_and_instructors_details`
  WHERE instructormanager_id IS NOT NULL AND instructor_user_id IS NOT NULL
),
-- the manager names on the instructor's latest QA record (kept as column F for the email page)
qa_mgr_names AS (
  SELECT LOWER(REPLACE(TRIM(instructor_user_id), '-', '')) AS iid,
         ARRAY_AGG(instructor_manager_name ORDER BY evaluation_creation_datetime DESC LIMIT 1)[OFFSET(0)] AS names
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_session_wise_qa_rating`
  WHERE instructor_manager_name IS NOT NULL AND instructor_user_id IS NOT NULL
  GROUP BY iid
),
-- capability manager = mapped managers (or names on the QA record) that are on the list of 14
cap_pairs AS (
  SELECT DISTINCT iid, manager, mid FROM (
    SELECT mv.iid, mv.manager, mv.mid FROM mgr_view mv JOIN cap_list c ON LOWER(mv.manager) = c.cap
    UNION ALL
    SELECT q.iid, TRIM(n) AS manager, CAST(NULL AS STRING) AS mid
    FROM qa_mgr_names q, UNNEST(SPLIT(q.names, ',')) AS n
    JOIN cap_list c ON LOWER(TRIM(n)) = c.cap
    WHERE q.iid NOT IN (SELECT mv2.iid FROM mgr_view mv2 JOIN cap_list c2 ON LOWER(mv2.manager) = c2.cap)
  )
),
cap_mgr AS (
  SELECT p.iid,
         STRING_AGG(DISTINCT p.manager, ', ' ORDER BY p.manager) AS names,
         STRING_AGG(DISTINCT p.mid, ', ' ORDER BY p.mid)         AS ids,
         STRING_AGG(DISTINCT ue.email, ', ' ORDER BY ue.email)   AS emails,
         COUNT(DISTINCT LOWER(p.manager))                        AS n
  FROM cap_pairs p LEFT JOIN user_emails ue ON ue.uid = p.mid
  GROUP BY p.iid
),

-- =====================================================================  QA (one score per session section)
sched AS (
  SELECT sid, ANY_VALUE(stype) AS session_type, ANY_VALUE(siid) AS iid,
         ARRAY_AGG(STRUCT(session_start_datetime, session_id, session_title)
                   ORDER BY session_start_datetime DESC LIMIT 1)[OFFSET(0)] AS x
  FROM (
    SELECT LOWER(REPLACE(TRIM(session_section_id), '-', '')) AS sid, UPPER(TRIM(session_type)) AS stype,
           LOWER(REPLACE(TRIM(instructor_user_id), '-', '')) AS siid, session_start_datetime, session_id, session_title
    FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_instructor_session_schedule_details`
    WHERE session_section_id IS NOT NULL
  )
  GROUP BY sid
  HAVING COUNT(DISTINCT stype) = 1 AND COUNT(DISTINCT IFNULL(siid, '')) = 1
),
rub_any AS (
  SELECT DISTINCT LOWER(REPLACE(TRIM(session_section_id), '-', '')) AS sid
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_session_wise_rubric_metrics_details`
),
qa_c AS (
  SELECT q.*,
         LOWER(REPLACE(TRIM(q.session_section_id), '-', '')) AS sid,
         LOWER(REPLACE(TRIM(q.instructor_user_id), '-', '')) AS q_iid,
         COUNT(*) OVER (PARTITION BY LOWER(REPLACE(TRIM(q.session_section_id), '-', ''))) AS n_same,
         ROW_NUMBER() OVER (PARTITION BY LOWER(REPLACE(TRIM(q.session_section_id), '-', ''))
                            ORDER BY q.evaluation_creation_datetime DESC, q.evaluation_id DESC) AS rn
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_session_wise_qa_rating` q
  WHERE UPPER(TRIM(q.evaluation_type)) = 'SCHEDULE' AND LOWER(q.status) = 'completed' AND q.final_score IS NOT NULL
),
qa_v AS (
  SELECT qa_c.q_iid AS iid, DATE(s.x.session_start_datetime) AS d, s.session_type,
         qa_c.evaluation_creation_datetime, qa_c.evaluation_id, qa_c.session_id, qa_c.session_section_id,
         qa_c.session_title, qa_c.final_score, qa_c.status, qa_c.report_url, qa_c.institute_name,
         qa_c.instructor_category, qa_c.n_same, qa_c.sid,
         qa_c.instructor_name AS q_name, qa_c.instructor_mail AS q_mail
  FROM qa_c
  JOIN sched s ON s.sid = qa_c.sid AND s.iid = qa_c.q_iid
  LEFT JOIN rub_any ra ON ra.sid = qa_c.sid
  CROSS JOIN params p
  WHERE qa_c.rn = 1
    AND s.session_type IN ('LECTURE', 'PRACTICE')
    AND DATE(s.x.session_start_datetime) BETWEEN LEAST(p.qa_lookback, p.from_8wk) AND p.run_date
    AND NOT (qa_c.final_score = 0 AND ra.sid IS NULL)
),
-- each instructor's last 3 QA days (oldest = 1), for instructors with a QA day in the last 14 days
day_avg AS (
  SELECT v.iid, v.d, ROUND(AVG(v.final_score), 2) AS avg, COUNT(*) AS n,
         ROW_NUMBER() OVER (PARTITION BY v.iid ORDER BY v.d DESC) AS rk
  FROM qa_v v CROSS JOIN params p
  WHERE v.d BETWEEN p.qa_lookback AND p.qa_end
  GROUP BY v.iid, v.d
),
eligible AS (
  SELECT a.iid FROM day_avg a CROSS JOIN params p
  GROUP BY a.iid, p.qa_recent HAVING MAX(a.d) >= p.qa_recent
),
last3 AS (
  SELECT a.*, COUNT(*) OVER (PARTITION BY a.iid) - a.rk + 1 AS k
  FROM day_avg a JOIN eligible USING (iid)
  WHERE a.rk <= 3
),
days_wide AS (
  SELECT iid,
         MAX(IF(k = 1, d, NULL)) AS d1, MAX(IF(k = 1, avg, NULL)) AS a1, MAX(IF(k = 1, n, NULL)) AS n1,
         MAX(IF(k = 2, d, NULL)) AS d2, MAX(IF(k = 2, avg, NULL)) AS a2, MAX(IF(k = 2, n, NULL)) AS n2,
         MAX(IF(k = 3, d, NULL)) AS d3, MAX(IF(k = 3, avg, NULL)) AS a3, MAX(IF(k = 3, n, NULL)) AS n3,
         ROUND(SUM(avg * n) / SUM(n), 2) AS avg3, SUM(n) AS n3_total,
         STRING_AGG(FORMAT('%t %.2f', d, avg), ' | ' ORDER BY d) AS days_text,
         TO_JSON_STRING(ARRAY_AGG(STRUCT(FORMAT_DATE('%Y-%m-%d', d) AS session_date, avg AS daily_average,
                                         n AS evaluation_count) ORDER BY d)) AS daily_summary_json
  FROM last3 GROUP BY iid
),
flags AS (
  SELECT w.iid,
         IFNULL(w.a1 < p.threshold AND w.a2 < p.threshold AND w.a3 < p.threshold, FALSE) AS low_qa
  FROM days_wide w CROSS JOIN params p
),

-- =====================================================================  page 1: instructor emails
rep AS (
  SELECT v.* FROM qa_v v JOIN last3 l ON l.iid = v.iid AND l.d = v.d
),
reports AS (
  SELECT iid,
         TO_JSON_STRING(ARRAY_AGG(STRUCT(
           FORMAT_DATE('%Y-%m-%d', d) AS session_date,
           CAST(evaluation_creation_datetime AS STRING) AS evaluation_creation_datetime,
           evaluation_id, session_id, session_section_id, session_title, final_score, status, report_url,
           institute_name, instructor_category, session_type,
           n_same AS evaluations_for_same_mapping,
           IF(n_same = 1, 'UNIQUE_MAPPING', 'AMBIGUOUS_MULTIPLE_QA_EVALUATIONS') AS rubric_mapping_status,
           'null' AS rubric_scores_json)
           ORDER BY d, evaluation_creation_datetime)) AS qa_reports_json,
         COUNTIF(session_type = 'LECTURE') AS lecture_n, COUNTIF(session_type = 'PRACTICE') AS practice_n,
         COUNT(*) AS total_n, ROUND(AVG(final_score), 2) AS period_average,
         ANY_VALUE(q_name) AS q_name, ANY_VALUE(q_mail) AS q_mail
  FROM rep GROUP BY iid
),
rub_rows AS (
  SELECT rep.iid, rep.d, r.session_section_id, r.session_id, r.session_title, r.session_start_datetime,
         r.name, r.`order` AS rubric_order, r.score, r.normalized_score, r.institute_name, r.instructor_category
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_session_wise_rubric_metrics_details` r
  JOIN rep ON rep.sid = LOWER(REPLACE(TRIM(r.session_section_id), '-', ''))
  WHERE TRUE
  QUALIFY ROW_NUMBER() OVER (PARTITION BY rep.sid, LOWER(TRIM(r.name)) ORDER BY r.session_start_datetime DESC) = 1
),
rub_sess AS (
  SELECT iid, d, session_section_id,
         MIN(session_id) AS session_id, MIN(session_title) AS session_title,
         MIN(session_start_datetime) AS session_start_datetime,
         ARRAY_AGG(STRUCT(session_start_datetime, session_id, session_section_id, session_title,
                          name AS rubric_name, rubric_order, score AS original_rubric_score,
                          normalized_score, institute_name, instructor_category)
                   ORDER BY rubric_order, name) AS rubric_scores
  FROM rub_rows GROUP BY iid, d, session_section_id
),
evidence AS (
  SELECT iid,
         TO_JSON_STRING(ARRAY_AGG(STRUCT(FORMAT_DATE('%Y-%m-%d', d) AS session_date, session_id, session_section_id,
                                         session_start_datetime, session_title, rubric_scores)
                                  ORDER BY session_start_datetime)) AS session_rubric_evidence_json
  FROM rub_sess GROUP BY iid
),
p_instructor_qa AS (
  SELECT
    FORMAT_TIMESTAMP('%d-%m-%Y %H:%M', CURRENT_TIMESTAMP())        AS exported_at_utc,
    FORMAT_DATE('%d-%m-%Y', p.run_date)                            AS export_date_ist,
    w.iid                                                          AS instructor_user_id,
    COALESCE(r.instructor_name, rp.q_name)                         AS instructor_name,
    COALESCE(ue.email, LOWER(TRIM(rp.q_mail)))                     AS instructor_mail,
    cm.names                                                       AS instructor_manager_name,
    r.institute_name,
    r.instructor_category,
    FORMAT_DATE('%d-%m-%Y', w.d1) AS day1_date, w.a1 AS day1_average, IFNULL(w.n1, 0) AS day1_evaluation_count,
    FORMAT_DATE('%d-%m-%Y', w.d2) AS day2_date, w.a2 AS day2_average, IFNULL(w.n2, 0) AS day2_evaluation_count,
    FORMAT_DATE('%d-%m-%Y', w.d3) AS day3_date, w.a3 AS day3_average, IFNULL(w.n3, 0) AS day3_evaluation_count,
    TO_JSON_STRING(STRUCT(w.a1 AS day1_average, w.a2 AS day2_average, w.a3 AS day3_average,
                          IFNULL(w.n1, 0) AS day1_evaluation_count, IFNULL(w.n2, 0) AS day2_evaluation_count,
                          IFNULL(w.n3, 0) AS day3_evaluation_count)) AS three_day_summary_json,
    w.daily_summary_json,
    rp.qa_reports_json,
    IFNULL(ev.session_rubric_evidence_json, '[]')                  AS session_rubric_evidence_json,
    r.instructor_status,
    cm.names                                                       AS capability_manager_name,
    cm.emails                                                      AS capability_manager_email,
    cm.ids                                                         AS capability_manager_id,
    CASE WHEN cm.iid IS NULL THEN 'NOT_FOUND' WHEN cm.n > 1 THEN 'AMBIGUOUS' ELSE 'DIRECT' END
                                                                   AS capability_manager_status,
    CONCAT('HAS_', CAST((SELECT COUNT(*) FROM UNNEST([w.d1, w.d2, w.d3]) x WHERE x IS NOT NULL) AS STRING), '_QA_DAYS')
                                                                   AS qa_data_status,
    rp.lecture_n AS lecture_evaluation_count, rp.practice_n AS practice_evaluation_count,
    rp.total_n AS total_evaluation_count,
    (SELECT COUNT(*) FROM UNNEST([w.d1, w.d2, w.d3]) x WHERE x IS NOT NULL) AS evaluated_days,
    rp.period_average,
    f.low_qa                                                       AS low_score_alert_3_consecutive_days,
    IF(f.low_qa, FORMAT_DATE('%d-%m-%Y', w.d1), NULL)              AS alert_run_start,
    IF(f.low_qa, FORMAT_DATE('%d-%m-%Y', w.d3), NULL)              AS alert_run_end
  FROM days_wide w
  CROSS JOIN params p
  JOIN reports rp          ON rp.iid = w.iid
  JOIN flags f             ON f.iid = w.iid
  LEFT JOIN evidence ev    ON ev.iid = w.iid
  LEFT JOIN roster r       ON r.iid = w.iid
  LEFT JOIN user_emails ue ON ue.uid = w.iid
  LEFT JOIN cap_mgr cm     ON cm.iid = w.iid
),

-- =====================================================================  page 2: drill-down
summary AS (
  SELECT s.*, LOWER(REPLACE(TRIM(s.session_section_id), '-', '')) AS sid
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_sessions_wise_summary_details` s
  CROSS JOIN params p
  WHERE DATE(s.session_start_datetime) BETWEEN p.from_8wk AND p.run_date
  QUALIFY ROW_NUMBER() OVER (PARTITION BY s.session_section_id ORDER BY s.evaluation_id IS NULL, s.session_start_datetime) = 1
),
-- the summary rows with the shared QA score (qs = NULL when the session has no QA score under the rules above)
sess AS (
  SELECT s.*, qv.final_score AS qs
  FROM summary s LEFT JOIN qa_v qv ON qv.sid = s.sid
),
-- each instructor's sessions in the 14-day window, merged sections counted once (workload)
inst_sess AS (
  SELECT iid, DATE(start_dt) AS day,
    FORMAT_DATETIME('%H:%M', start_dt) AS start_time, FORMAT_DATETIME('%H:%M', MAX(end_dt)) AS end_time,
    MAX(DATETIME_DIFF(end_dt, start_dt, MINUTE)) AS minutes,
    ANY_VALUE(session_title) AS session_title, ANY_VALUE(session_type) AS session_type,
    ANY_VALUE(session_status) AS session_status,
    STRING_AGG(DISTINCT institute_name, ', ') AS institute_name,
    STRING_AGG(DISTINCT section_name, ', ' ORDER BY section_name) AS sections,
    MAX(qs) AS qa_rating
  FROM (
    SELECT LOWER(REPLACE(TRIM(s.instructor_user_id), '-', '')) AS iid, COALESCE(s.session_id, s.sid) AS skey,
      s.session_start_datetime AS start_dt, s.session_end_datetime AS end_dt, s.session_title, s.session_type,
      s.session_status, s.institute_name, s.section_name, s.qs
    FROM sess s CROSS JOIN params p
    WHERE DATE(s.session_start_datetime) BETWEEN p.from_date AND p.run_date AND s.instructor_user_id IS NOT NULL
  )
  GROUP BY iid, skey, start_dt
),
p01 AS (
  SELECT i.institute_id, i.institute_name, i.institute_type, i.institue_location AS location,
         sec.sections, sec.batches, stu.total_students
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_institute_details` i
  LEFT JOIN (
    SELECT institute_id, COUNT(DISTINCT section_id) AS sections, COUNT(DISTINCT batch_id) AS batches
    FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_institute_wise_section_details`
    GROUP BY institute_id) sec USING (institute_id)
  LEFT JOIN (
    SELECT institute_id, COUNT(DISTINCT user_id) AS total_students
    FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_users_core_backend_master_db`
    GROUP BY institute_id) stu USING (institute_id)
),
p02 AS (
  SELECT institute_id, institute_name, section_id, section_name, batch_name, derived_semester_title AS semester,
    DATE(session_start_datetime) AS day,
    COUNT(*) AS classes,
    COUNTIF(session_status = 'COMPLETED') AS completed,
    COUNTIF(session_status = 'PARTIALLY_COMPLETED') AS partially_completed,
    COUNTIF(session_status = 'PENDING') AS pending,
    COUNTIF(session_status = 'CANCELLED') AS cancelled,
    COUNTIF(session_status = 'SKIPPED') AS skipped,
    COUNTIF(session_status NOT IN ('COMPLETED','PARTIALLY_COMPLETED','PENDING','CANCELLED','SKIPPED')) AS other_status,
    COUNTIF(session_type = 'LECTURE') AS lecture, COUNTIF(session_type = 'PRACTICE') AS practice,
    COUNTIF(session_type = 'EXAM') AS exam, COUNTIF(session_type = 'BREAK') AS break_type,
    COUNTIF(session_type NOT IN ('LECTURE','PRACTICE','EXAM','BREAK')) AS other_type,
    COUNTIF(mode_of_delivery = 'OFFLINE') AS offline, COUNTIF(mode_of_delivery = 'ONLINE') AS online,
    COUNTIF(is_merged) AS merged,
    FORMAT_DATETIME('%H:%M', MIN(session_start_datetime)) AS first_start,
    FORMAT_DATETIME('%H:%M', MAX(session_end_datetime)) AS last_end,
    DATETIME_DIFF(MAX(session_end_datetime), MIN(session_start_datetime), MINUTE) AS span_mins,
    SUM(DATETIME_DIFF(session_end_datetime, session_start_datetime, MINUTE)) AS class_mins,
    COUNTIF(recording_flow IS NOT NULL) AS with_recording_info,
    COUNTIF(recording_flow IS NOT NULL AND recording_flow <> 'NO_RECORDING') AS recorded,
    COUNTIF(recording_flow = 'NO_RECORDING') AS no_recording,
    COUNTIF(evaluation_id IS NOT NULL) AS evaluated,
    COUNTIF(recording_flow <> 'NO_RECORDING' AND evaluation_id IS NULL) AS recorded_not_evaluated,
    SUM(qs) AS qa_sum, COUNT(qs) AS qa_n,
    SUM(IF(qs IS NOT NULL, performance_rating, NULL)) AS perf_sum, COUNTIF(qs IS NOT NULL AND performance_rating IS NOT NULL) AS perf_n,
    SUM(IF(qs IS NOT NULL, overall_grooming_score, NULL)) AS grooming_sum, COUNTIF(qs IS NOT NULL AND overall_grooming_score IS NOT NULL) AS grooming_n,
    COUNTIF(qs = 0) AS zero_qa,
    COUNTIF(qa_report_open_status IS NOT NULL) AS report_known,
    COUNTIF(qa_report_open_status = 'Yes') AS report_opened,
    COUNTIF(flag_status = 'Flagged') AS flagged,
    COUNTIF(malpractice_status = 'Yes') AS malpractice,
    COUNT(DISTINCT instructor_user_id) AS instructors
  FROM sess CROSS JOIN params p
  WHERE DATE(session_start_datetime) BETWEEN p.from_date AND p.run_date
  GROUP BY institute_id, institute_name, section_id, section_name, batch_name, semester, day
),
p03 AS (
  SELECT institute_name, section_name, DATE(session_start_datetime) AS day,
    FORMAT_DATETIME('%H:%M', session_start_datetime) AS start_time, FORMAT_DATETIME('%H:%M', session_end_datetime) AS end_time,
    session_title, session_type, session_status, instructor_name, recording_flow, qs AS qa_rating,
    recording_flow = 'NO_RECORDING' AS no_recording,
    recording_flow <> 'NO_RECORDING' AND evaluation_id IS NULL AS recorded_not_evaluated,
    session_status = 'PENDING' AND DATE(session_start_datetime) < p.run_date AS pending_after_date,
    session_status = 'PARTIALLY_COMPLETED' AS partially_completed,
    malpractice_status = 'Yes' AS malpractice,
    flag_status = 'Flagged' AS flagged,
    evaluation_id IS NOT NULL AND qa_report_open_status = 'No' AS report_not_opened,
    IFNULL(qs = 0, FALSE) AS zero_qa
  FROM sess CROSS JOIN params p
  WHERE DATE(session_start_datetime) BETWEEN DATE_SUB(p.run_date, INTERVAL 6 DAY) AND p.run_date
    AND (recording_flow = 'NO_RECORDING'
         OR (recording_flow <> 'NO_RECORDING' AND evaluation_id IS NULL)
         OR (session_status = 'PENDING' AND DATE(session_start_datetime) < p.run_date)
         OR session_status = 'PARTIALLY_COMPLETED' OR malpractice_status = 'Yes' OR flag_status = 'Flagged'
         OR (evaluation_id IS NOT NULL AND qa_report_open_status = 'No') OR qs = 0)
),
p04 AS (
  SELECT institute_id, institute_name, section_id, section_name, semester_title AS semester,
    COALESCE(course_category, category) AS subject, session_status,
    DATE(session_start_datetime) <= p.run_date AS due,
    COUNT(DISTINCT session_id) AS sessions
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_schedule_details_as_per_prod_sequence`
  CROSS JOIN params p
  WHERE is_current_semester = 1
  GROUP BY institute_id, institute_name, section_id, section_name, semester, subject, session_status, due
),
p05 AS (
  SELECT pl.*, g.due_by_today, g.completed, g.partially_completed, g.pending_after_date
  FROM (
    SELECT institute_id, institute_name, section_id, section_name, derived_semester_title AS semester,
      course_category AS subject, semester_course_id, semester_course_title AS course,
      COUNT(DISTINCT session_id) AS planned
    FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_institute_wise_semester_course_session_details`
    WHERE is_current_semester = 1
    GROUP BY institute_id, institute_name, section_id, section_name, semester, subject, semester_course_id, course
  ) pl
  LEFT JOIN (
    SELECT section_id, semester_course_id,
      COUNT(DISTINCT IF(DATE(session_start_datetime) <= p.run_date, session_id, NULL)) AS due_by_today,
      COUNT(DISTINCT IF(session_status = 'COMPLETED', session_id, NULL)) AS completed,
      COUNT(DISTINCT IF(session_status = 'PARTIALLY_COMPLETED', session_id, NULL)) AS partially_completed,
      COUNT(DISTINCT IF(session_status = 'PENDING' AND DATE(session_start_datetime) < p.run_date, session_id, NULL)) AS pending_after_date
    FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_schedule_details_as_per_prod_sequence`
    CROSS JOIN params p
    WHERE is_current_semester = 1
    GROUP BY section_id, semester_course_id
  ) g USING (section_id, semester_course_id)
),
p06 AS (
  SELECT institute_id, institute_name, TRIM(sec) AS section_id_one,
    COUNT(DISTINCT session_id) AS classes_tomorrow,
    FORMAT_DATETIME('%H:%M', MIN(session_start_datetime)) AS first_start
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_instructor_session_schedule_details`,
    UNNEST(SPLIT(section_id, ',')) AS sec
  CROSS JOIN params p
  WHERE DATE(session_start_datetime) = p.next_day
  GROUP BY institute_id, institute_name, section_id_one
),
p07 AS (
  SELECT DATE_TRUNC(DATE(session_start_datetime), WEEK(MONDAY)) AS week,
    institute_id, institute_name, COALESCE(course_category, '(blank)') AS subject,
    COUNTIF(session_type IN ('LECTURE','PRACTICE')) AS lp_sessions,
    COUNTIF(session_type IN ('LECTURE','PRACTICE') AND recording_flow = 'NO_RECORDING') AS lp_no_recording,
    COUNTIF(session_type IN ('LECTURE','PRACTICE') AND evaluation_id IS NOT NULL) AS lp_evaluated,
    SUM(qs) AS qa_sum, COUNT(qs) AS qa_n, COUNTIF(qs = 0) AS zero_qa
  FROM sess
  GROUP BY week, institute_id, institute_name, subject
),
p08 AS (
  SELECT institute_name, COALESCE(instructor_category, '(blank)') AS subject, name AS rubric,
    SUM(normalized_score) AS score_sum, COUNT(normalized_score) AS n
  FROM (
    SELECT r.* FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_session_wise_rubric_metrics_details` r
    CROSS JOIN params p
    WHERE DATE(r.session_start_datetime) BETWEEN p.from_8wk AND p.run_date
    QUALIFY ROW_NUMBER() OVER (PARTITION BY r.session_section_id, r.name ORDER BY r.session_start_datetime DESC) = 1
  )
  GROUP BY institute_name, subject, rubric
),
p09 AS (
  SELECT institute_id, institute_name, section_id, section_name, date AS day,
    COUNT(*) AS records,
    COUNTIF(is_attendance_submitted = 1 AND COALESCE(marking_method, '') <> 'NOT_SUBMITTED') AS submitted,
    COUNTIF(is_attendance_submitted = 1 AND COALESCE(marking_method, '') <> 'NOT_SUBMITTED' AND attendance_status = 'PRESENT') AS present,
    COUNTIF(is_attendance_submitted = 1 AND COALESCE(marking_method, '') <> 'NOT_SUBMITTED' AND attendance_status = 'ABSENT') AS absent,
    COUNTIF(is_attendance_submitted = 0 OR marking_method = 'NOT_SUBMITTED') AS not_submitted,
    COUNT(DISTINCT student_user_id) AS students,
    COUNT(DISTINCT IF(is_attendance_submitted = 1 AND COALESCE(marking_method, '') <> 'NOT_SUBMITTED' AND attendance_status = 'PRESENT', student_user_id, NULL)) AS students_present,
    COUNTIF(marking_method = 'AUTO_FACE_RECOGNITION') AS by_face_recognition,
    COUNTIF(marking_method = 'MANUAL') AS by_manual
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_students_overall_attendance_details`
  CROSS JOIN params p
  WHERE date BETWEEN p.from_date AND p.run_date
  GROUP BY institute_id, institute_name, section_id, section_name, day
),
p10 AS (
  SELECT institute_name, section_name, DATE(feedback_submission_datetime) AS day,
    COALESCE(course_category, '(blank)') AS subject,
    COUNT(*) AS responses, COUNT(DISTINCT student_user_id) AS students,
    SUM(learning_session_understanding_rating) AS understanding_sum, COUNT(learning_session_understanding_rating) AS understanding_n,
    SUM(learning_session_teaching_quality_rating) AS teaching_sum, COUNT(learning_session_teaching_quality_rating) AS teaching_n,
    SUM(practice_session_guidance_clarity_rating) AS guidance_sum, COUNT(practice_session_guidance_clarity_rating) AS guidance_n,
    COUNTIF(sentiment = 'positive') AS positive, COUNTIF(sentiment = 'neutral') AS neutral, COUNTIF(sentiment = 'negative') AS negative
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_session_wise_user_feedback_details_with_sentiment_type`
  CROSS JOIN params p
  WHERE DATE(feedback_submission_datetime) BETWEEN p.from_date AND p.run_date
  GROUP BY institute_name, section_name, day, subject
),
p11 AS (
  WITH load AS (
    SELECT iid,
      COUNT(*) AS sessions_14d, COUNTIF(session_type IN ('LECTURE','PRACTICE')) AS lp_sessions_14d,
      SUM(minutes) AS minutes_14d,
      STRING_AGG(DISTINCT institute_name, ', ') AS taught_at,
      COUNTIF(qa_rating = 0) AS zero_qa_14d
    FROM inst_sess
    GROUP BY iid
  ), chk AS (
    SELECT LOWER(REPLACE(TRIM(instructor_user_id), '-', '')) AS iid, COUNT(*) AS checkin_days,
      COUNTIF(COALESCE(derived_check_in_status, check_in_status) IS NOT NULL) AS checked_in_days,
      COUNTIF(late_by_mins > 0) AS late_days
    FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_instructor_wise_attendance_details_transformed`
    CROSS JOIN params p
    WHERE date BETWEEN p.from_date AND p.run_date
    GROUP BY iid
  ), con AS (
    SELECT LOWER(REPLACE(TRIM(instructor_user_id), '-', '')) AS iid,
      COUNTIF(DATE_TRUNC(week_start_date, WEEK(MONDAY)) = p.week_start AND status = 'Completed') AS connects_done_this_week,
      STRING_AGG(IF(DATE_TRUNC(week_start_date, WEEK(MONDAY)) = p.week_start, status, NULL), ', ') AS connect_statuses_this_week,
      MAX(IF(status = 'Completed', scheduled_date, NULL)) AS last_completed_connect
    FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_manager_connect_session_details`
    CROSS JOIN params p
    GROUP BY iid
  ), ids AS (
    SELECT iid FROM roster UNION DISTINCT SELECT iid FROM load UNION DISTINCT SELECT iid FROM days_wide
  )
  SELECT ids.iid AS instructor_user_id, r.instructor_name, r.instructor_category AS category,
    r.institute_name AS home_institute, r.instructor_status AS status,
    cm.names AS managers, ue.email AS instructor_mail,
    load.taught_at, load.sessions_14d, load.lp_sessions_14d, load.minutes_14d, load.zero_qa_14d,
    w.avg3 AS qa_last3_avg, w.n3_total AS qa_last3_n, w.days_text AS qa_last3_days,
    IFNULL(f.low_qa, FALSE) AS low_qa_flag,
    chk.checkin_days, chk.checked_in_days, chk.late_days,
    con.connects_done_this_week, con.connect_statuses_this_week, con.last_completed_connect
  FROM ids
  LEFT JOIN roster r USING (iid) LEFT JOIN load USING (iid) LEFT JOIN days_wide w USING (iid)
  LEFT JOIN flags f USING (iid) LEFT JOIN chk USING (iid) LEFT JOIN con USING (iid)
  LEFT JOIN cap_mgr cm USING (iid) LEFT JOIN user_emails ue ON ue.uid = ids.iid
),
p12 AS (
  -- instructors per capability manager use the same rule as everywhere else (cap_pairs)
  WITH m AS (
    SELECT LOWER(cp.manager) AS mkey, ANY_VALUE(cp.manager) AS manager,
      ANY_VALUE(mv.manager_category) AS category,
      ARRAY_AGG(DISTINCT cp.mid IGNORE NULLS LIMIT 1)[SAFE_OFFSET(0)] AS manager_id,
      COUNT(DISTINCT cp.iid) AS instructors,
      COUNT(DISTINCT IF(r.instructor_status = 'ACTIVE', cp.iid, NULL)) AS active_instructors
    FROM cap_pairs cp
    LEFT JOIN roster r USING (iid)
    LEFT JOIN mgr_view mv ON mv.mid = cp.mid
    GROUP BY mkey
  ), ids AS (
    SELECT DISTINCT LOWER(manager) AS mkey, mid FROM mgr_view WHERE LOWER(manager) IN (SELECT cap FROM cap_list)
  ), c AS (
    SELECT ids.mkey, DATE_TRUNC(x.week_start_date, WEEK(MONDAY)) AS week,
      COUNT(*) AS connects, COUNTIF(x.status = 'Completed') AS completed, COUNTIF(x.status = 'Missed') AS missed,
      COUNTIF(x.status = 'Rescheduled') AS rescheduled, COUNTIF(x.status = 'Reschedule Required') AS reschedule_required,
      COUNTIF(x.status = 'Scheduled') AS scheduled,
      COUNT(DISTINCT IF(x.status = 'Completed', x.instructor_user_id, NULL)) AS instructors_connected,
      SUM(IF(x.status = 'Completed', x.qa_score, NULL)) AS connect_qa_sum,
      COUNTIF(x.status = 'Completed' AND x.qa_score IS NOT NULL) AS connect_qa_n,
      COUNTIF(x.status = 'Completed' AND x.transcript_status = 'Failed') AS transcript_failed
    FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_manager_connect_session_details` x
    JOIN ids ON ids.mid = LOWER(REPLACE(TRIM(x.instructor_manager_user_id), '-', ''))
    CROSS JOIN params p
    WHERE x.week_start_date >= p.from_8wk
    GROUP BY ids.mkey, week
  )
  SELECT m.manager_id, m.manager, m.category, m.instructors, m.active_instructors,
    c.week, c.connects, c.completed, c.missed, c.rescheduled, c.reschedule_required, c.scheduled,
    c.instructors_connected, c.connect_qa_sum, c.connect_qa_n, c.transcript_failed
  FROM m LEFT JOIN c USING (mkey)
),
p13 AS (
  SELECT institute_name, assigned_team, ticket_status, category_of_the_issue AS category, sub_category_of_the_issue AS sub_category,
    COUNT(*) AS tickets,
    STRING_AGG(DISTINCT sla_breach_status, ', ') AS sla_breach_statuses,
    MIN(IF(ticket_closure_date_time IS NULL, DATE(ticket_creation_datetime), NULL)) AS oldest_open_created,
    STRING_AGG(DISTINCT LOWER(REPLACE(instructor_user_id, '-', '')), ',') AS instructor_ids
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_instructor_ticketing_details`
  CROSS JOIN params p
  WHERE DATE(ticket_creation_datetime) BETWEEN p.month_start AND p.run_date
  GROUP BY institute_name, assigned_team, ticket_status, category, sub_category
),
p14 AS (
  SELECT iid AS instructor_user_id, day, start_time, end_time, minutes, session_title, session_type, session_status,
    institute_name, sections, qa_rating
  FROM inst_sess
),
p15 AS (
  SELECT v.iid AS instructor_user_id, v.d AS day, ROUND(SUM(v.final_score), 4) AS qa_sum, COUNT(*) AS qa_n
  FROM qa_v v CROSS JOIN params p
  WHERE v.d BETWEEN p.qa_lookback AND p.qa_end
  GROUP BY v.iid, v.d
),
p00 AS (
  SELECT run_date, from_date, next_day, qa_end, qa_lookback AS qa_from, threshold,
         FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%SZ', CURRENT_TIMESTAMP()) AS exported_at_utc,
         'niat_all_in_one v3' AS query_version
  FROM params
)

SELECT 'meta' AS part, TO_JSON_STRING(t) AS row_json FROM p00 t
UNION ALL SELECT '01_universities',        TO_JSON_STRING(t) FROM p01 t
UNION ALL SELECT '02_section_day',         TO_JSON_STRING(t) FROM p02 t
UNION ALL SELECT '03_attention_classes',   TO_JSON_STRING(t) FROM p03 t
UNION ALL SELECT '04_subject_status',      TO_JSON_STRING(t) FROM p04 t
UNION ALL SELECT '05_curriculum_progress', TO_JSON_STRING(t) FROM p05 t
UNION ALL SELECT '06_next_day',            TO_JSON_STRING(t) FROM p06 t
UNION ALL SELECT '07_qa_weekly',           TO_JSON_STRING(t) FROM p07 t
UNION ALL SELECT '08_rubric',              TO_JSON_STRING(t) FROM p08 t
UNION ALL SELECT '09_students_daily',      TO_JSON_STRING(t) FROM p09 t
UNION ALL SELECT '10_feedback_daily',      TO_JSON_STRING(t) FROM p10 t
UNION ALL SELECT '11_instructors',         TO_JSON_STRING(t) FROM p11 t
UNION ALL SELECT '12_managers_weekly',     TO_JSON_STRING(t) FROM p12 t
UNION ALL SELECT '13_tickets_month',       TO_JSON_STRING(t) FROM p13 t
UNION ALL SELECT '14_sessions',            TO_JSON_STRING(t) FROM p14 t
UNION ALL SELECT '15_qa_days',             TO_JSON_STRING(t) FROM p15 t
UNION ALL SELECT 'instructor_qa_last3_export', TO_JSON_STRING(t) FROM p_instructor_qa t
ORDER BY part;
