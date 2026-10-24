-- 5_instructor_qa_last3_export.csv : the file the existing QA email page (page 1) uploads.
-- Same columns as instructor_qa_7day_export_with_managers, but day1-day3 are each instructor's
-- LAST 3 DAYS WITH QA (oldest = day1), not the last 3 calendar days.
--
-- Who is included: instructors with at least one QA day in the last 14 days (up to yesterday, IST).
--   Days are looked for within the last 60 days, so someone reviewed on 2, 9 and 20 Oct still has 3 days.
--   Instructors with only 1 or 2 QA days are kept with the missing days empty (the app does not flag them).
-- Rules (same as 1_qa_daily): SCHEDULE + completed; latest report per session section; report belongs
--   to the scheduled session's instructor; LECTURE and PRACTICE only; a 0 without rubric rows is dropped.
-- low_score_alert_3_consecutive_days = all three QA days have a daily average below 5.
WITH params AS (
  SELECT
    DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 1 DAY)  AS end_date,
    DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 60 DAY) AS lookback_start,
    DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 14 DAY) AS recent_start,
    5.0                                                     AS alert_threshold
),

-- ---------- people ----------
roster AS (
  SELECT LOWER(REPLACE(TRIM(instructor_user_id), '-', '')) AS iid,
         instructor_name, instructor_category, instructor_status, institute_name
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
-- manager list exactly as shown on the instructor's latest QA record (column F, used by the app)
mlist AS (
  SELECT LOWER(REPLACE(TRIM(instructor_user_id), '-', '')) AS iid,
         ARRAY_AGG(instructor_manager_name ORDER BY evaluation_creation_datetime DESC LIMIT 1)[OFFSET(0)] AS instructor_manager_name
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_session_wise_qa_rating`
  WHERE instructor_manager_name IS NOT NULL
  GROUP BY 1
),

-- ---------- capability manager (same rule as roster_qa_export.sql) ----------
mgr_view AS (
  SELECT DISTINCT LOWER(REPLACE(TRIM(instructor_user_id), '-', ''))   AS iid,
                  LOWER(REPLACE(TRIM(instructormanager_id), '-', '')) AS mid,
                  instructor_manager, instructor_manager_category
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_instructor_managers_and_instructors_details`
  WHERE instructormanager_id IS NOT NULL
),
mgr_dir AS (
  SELECT mid, ANY_VALUE(instructor_manager) AS manager_name,
         ANY_VALUE(instructor_manager_category) AS manager_category, COUNT(DISTINCT iid) AS scope_size
  FROM mgr_view GROUP BY mid
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
mgr_candidates AS (
  SELECT d.iid, d.mid, IF(COUNT(*) OVER (PARTITION BY d.iid) = 1, 1, 2) AS src_rank
  FROM direct_mgr d
  JOIN roster r USING (iid)
  LEFT JOIN mgr_dir md USING (mid)
  WHERE TRUE
  QUALIFY COUNT(*) OVER (PARTITION BY d.iid) = 1 OR md.manager_category = r.instructor_category
  UNION ALL
  SELECT r.iid, mv.mid, 3
  FROM roster r
  JOIN mgr_view mv ON mv.iid = r.iid AND mv.instructor_manager_category = r.instructor_category
  JOIN mgr_dir md  ON md.mid = mv.mid AND md.scope_size < 300
  WHERE r.iid NOT IN (SELECT iid FROM direct_mgr)
),
capability_manager AS (
  SELECT c.iid,
         COUNT(DISTINCT c.mid) AS n_candidates,
         CASE MIN(c.src_rank) WHEN 1 THEN 'DIRECT' WHEN 2 THEN 'DIRECT_RESOLVED' ELSE 'INFERRED_FROM_MANAGER_VIEW' END AS source,
         STRING_AGG(DISTINCT c.mid, ', ')           AS manager_ids,
         STRING_AGG(DISTINCT md.manager_name, ', ') AS manager_names,
         STRING_AGG(DISTINCT ue.email, ', ')        AS manager_emails
  FROM mgr_candidates c
  LEFT JOIN mgr_dir md     ON md.mid = c.mid
  LEFT JOIN user_emails ue ON ue.uid = c.mid
  GROUP BY c.iid
),

-- ---------- QA ----------
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
  WHERE UPPER(TRIM(q.evaluation_type)) = 'SCHEDULE'
    AND LOWER(q.status) = 'completed'
    AND q.final_score IS NOT NULL
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
    AND DATE(s.x.session_start_datetime) BETWEEN p.lookback_start AND p.end_date
    AND NOT (qa_c.final_score = 0 AND ra.sid IS NULL)
),
day_avg AS (
  SELECT iid, d, ROUND(AVG(final_score), 2) AS avg, COUNT(*) AS n,
         ROW_NUMBER() OVER (PARTITION BY iid ORDER BY d DESC) AS rk
  FROM qa_v GROUP BY iid, d
),
eligible AS (
  SELECT iid FROM day_avg CROSS JOIN params p
  GROUP BY iid, p.recent_start HAVING MAX(d) >= p.recent_start
),
last3 AS (
  SELECT a.*, COUNT(*) OVER (PARTITION BY a.iid) - a.rk + 1 AS k     -- k = 1 oldest .. 3 newest
  FROM day_avg a JOIN eligible USING (iid)
  WHERE a.rk <= 3
),
days_wide AS (
  SELECT iid,
         MAX(IF(k = 1, d, NULL)) AS d1, MAX(IF(k = 1, avg, NULL)) AS a1, MAX(IF(k = 1, n, NULL)) AS n1,
         MAX(IF(k = 2, d, NULL)) AS d2, MAX(IF(k = 2, avg, NULL)) AS a2, MAX(IF(k = 2, n, NULL)) AS n2,
         MAX(IF(k = 3, d, NULL)) AS d3, MAX(IF(k = 3, avg, NULL)) AS a3, MAX(IF(k = 3, n, NULL)) AS n3,
         TO_JSON_STRING(ARRAY_AGG(STRUCT(FORMAT_DATE('%Y-%m-%d', d) AS session_date, avg AS daily_average,
                                         n AS evaluation_count) ORDER BY d)) AS daily_summary_json
  FROM last3 GROUP BY iid
),
rep AS (
  SELECT v.*
  FROM qa_v v JOIN last3 l ON l.iid = v.iid AND l.d = v.d
),
reports AS (
  SELECT iid,
         TO_JSON_STRING(ARRAY_AGG(STRUCT(
           FORMAT_DATE('%Y-%m-%d', d)                 AS session_date,
           CAST(evaluation_creation_datetime AS STRING) AS evaluation_creation_datetime,
           evaluation_id, session_id, session_section_id, session_title, final_score, status, report_url,
           institute_name, instructor_category, session_type,
           n_same                                      AS evaluations_for_same_mapping,
           IF(n_same = 1, 'UNIQUE_MAPPING', 'AMBIGUOUS_MULTIPLE_QA_EVALUATIONS') AS rubric_mapping_status,
           'null'                                      AS rubric_scores_json)
           ORDER BY d, evaluation_creation_datetime)) AS qa_reports_json,
         COUNTIF(session_type = 'LECTURE')  AS lecture_n,
         COUNTIF(session_type = 'PRACTICE') AS practice_n,
         COUNT(*)                           AS total_n,
         ROUND(AVG(final_score), 2)         AS period_average,
         ANY_VALUE(q_name)                  AS q_name,
         ANY_VALUE(q_mail)                  AS q_mail
  FROM rep GROUP BY iid
),
-- rubric evidence for the kept sessions (one row per section + criterion)
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
)

SELECT
  FORMAT_TIMESTAMP('%d-%m-%Y %H:%M', CURRENT_TIMESTAMP())                  AS exported_at_utc,
  FORMAT_DATE('%d-%m-%Y', CURRENT_DATE('Asia/Kolkata'))                    AS export_date_ist,
  w.iid                                                                    AS instructor_user_id,
  COALESCE(r.instructor_name, rp.q_name)                                   AS instructor_name,
  COALESCE(ue.email, LOWER(TRIM(rp.q_mail)))                               AS instructor_mail,
  ml.instructor_manager_name,
  r.institute_name,
  r.instructor_category,
  FORMAT_DATE('%d-%m-%Y', w.d1) AS day1_date, w.a1 AS day1_average, IFNULL(w.n1, 0) AS day1_evaluation_count,
  FORMAT_DATE('%d-%m-%Y', w.d2) AS day2_date, w.a2 AS day2_average, IFNULL(w.n2, 0) AS day2_evaluation_count,
  FORMAT_DATE('%d-%m-%Y', w.d3) AS day3_date, w.a3 AS day3_average, IFNULL(w.n3, 0) AS day3_evaluation_count,
  TO_JSON_STRING(STRUCT(w.a1 AS day1_average, w.a2 AS day2_average, w.a3 AS day3_average,
                        IFNULL(w.n1, 0) AS day1_evaluation_count, IFNULL(w.n2, 0) AS day2_evaluation_count,
                        IFNULL(w.n3, 0) AS day3_evaluation_count))         AS three_day_summary_json,
  w.daily_summary_json,
  rp.qa_reports_json,
  IFNULL(ev.session_rubric_evidence_json, '[]')                            AS session_rubric_evidence_json,
  r.instructor_status,
  cm.manager_names                                                         AS capability_manager_name,
  cm.manager_emails                                                        AS capability_manager_email,
  cm.manager_ids                                                           AS capability_manager_id,
  CASE
    WHEN cm.iid IS NULL           THEN 'NOT_FOUND'
    WHEN cm.n_candidates > 1      THEN CONCAT('AMBIGUOUS_', cm.source)
    WHEN cm.manager_emails IS NULL THEN CONCAT(cm.source, '_NO_EMAIL')
    ELSE cm.source
  END                                                                      AS capability_manager_status,
  CONCAT('HAS_', CAST((SELECT COUNT(*) FROM UNNEST([w.d1, w.d2, w.d3]) x WHERE x IS NOT NULL) AS STRING),
         '_QA_DAYS')                                                       AS qa_data_status,
  rp.lecture_n                                                             AS lecture_evaluation_count,
  rp.practice_n                                                            AS practice_evaluation_count,
  rp.total_n                                                               AS total_evaluation_count,
  (SELECT COUNT(*) FROM UNNEST([w.d1, w.d2, w.d3]) x WHERE x IS NOT NULL)  AS evaluated_days,
  rp.period_average,
  IFNULL(w.a1 < p.alert_threshold AND w.a2 < p.alert_threshold AND w.a3 < p.alert_threshold, FALSE)
                                                                           AS low_score_alert_3_consecutive_days,
  IF(IFNULL(w.a1 < p.alert_threshold AND w.a2 < p.alert_threshold AND w.a3 < p.alert_threshold, FALSE),
     FORMAT_DATE('%d-%m-%Y', w.d1), NULL)                                  AS alert_run_start,
  IF(IFNULL(w.a1 < p.alert_threshold AND w.a2 < p.alert_threshold AND w.a3 < p.alert_threshold, FALSE),
     FORMAT_DATE('%d-%m-%Y', w.d3), NULL)                                  AS alert_run_end
FROM days_wide w
CROSS JOIN params p
JOIN reports rp            ON rp.iid = w.iid
LEFT JOIN evidence ev      ON ev.iid = w.iid
LEFT JOIN roster r         ON r.iid = w.iid
LEFT JOIN user_emails ue   ON ue.uid = w.iid
LEFT JOIN mlist ml         ON ml.iid = w.iid
LEFT JOIN capability_manager cm ON cm.iid = w.iid
ORDER BY LOWER(COALESCE(r.instructor_name, rp.q_name));
