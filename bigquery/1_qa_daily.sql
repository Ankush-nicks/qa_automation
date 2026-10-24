-- 1_qa_daily.csv : QA by day x campus x subject x session type x instructor
-- Used by: campus analysis (idle campuses, QA coverage, watchlists, subject + rubric drivers),
--          instructor "last 3 QA days" average and bottom quarter, manager portfolio averages.
--
-- Window : from 28 days before the 1st of the reporting month to yesterday (IST),
--          so every day of the month has its 14 + 14 day comparison.
-- Rules  : SCHEDULE evaluations only (CONNECT excluded); completed reports only;
--          one report per session section = latest completed one; report must belong to the
--          scheduled session's instructor; scores only for LECTURE and PRACTICE;
--          a 0 counts as a score only when rubric rows exist, otherwise it is "unscored".
--          All session types are kept in sessions_scheduled so idle campuses can be found.
--          Averages: SUM(score_sum) / SUM(scored) for any grouping - never average the averages.
WITH params AS (
  SELECT
    DATE_SUB(DATE_TRUNC(DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 1 DAY), MONTH), INTERVAL 28 DAY) AS start_date,
    DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 1 DAY) AS end_date
),

-- schedule: one row per session section; keys with more than one session type or instructor are dropped
sched AS (
  SELECT
    sid,
    ANY_VALUE(stype) AS session_type,
    ANY_VALUE(siid)  AS iid,
    ARRAY_AGG(STRUCT(session_start_datetime, institute_id, institute_name, institute_type,
                     semester_course_title, course_category)
              ORDER BY session_start_datetime DESC LIMIT 1)[OFFSET(0)] AS x
  FROM (
    SELECT LOWER(REPLACE(TRIM(session_section_id), '-', '')) AS sid,
           UPPER(TRIM(session_type))                         AS stype,
           LOWER(REPLACE(TRIM(instructor_user_id), '-', '')) AS siid,
           session_start_datetime, institute_id, institute_name, institute_type,
           semester_course_title, course_category
    FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_instructor_session_schedule_details`
    WHERE session_section_id IS NOT NULL
  )
  GROUP BY sid
  HAVING COUNT(DISTINCT stype) = 1 AND COUNT(DISTINCT IFNULL(siid, '')) = 1
),

s AS (
  SELECT sd.sid, sd.session_type, sd.iid,
         DATE(sd.x.session_start_datetime) AS session_date,
         sd.x.institute_id   AS campus_id,
         sd.x.institute_name AS campus_name,
         sd.x.institute_type AS campus_type,
         sd.x.semester_course_title AS subject,
         sd.x.course_category
  FROM sched sd CROSS JOIN params p
  WHERE DATE(sd.x.session_start_datetime) BETWEEN p.start_date AND p.end_date
),

-- QA reports per section + instructor: any report, completed reports, latest completed score
qa_sec AS (
  SELECT
    LOWER(REPLACE(TRIM(session_section_id), '-', '')) AS sid,
    LOWER(REPLACE(TRIM(instructor_user_id), '-', '')) AS iid,
    COUNT(*)                                AS reports,
    COUNTIF(LOWER(status) = 'completed')    AS completed_reports,
    ARRAY_AGG(IF(LOWER(status) = 'completed' AND final_score IS NOT NULL, final_score, NULL) IGNORE NULLS
              ORDER BY evaluation_creation_datetime DESC, evaluation_id DESC LIMIT 1)[SAFE_OFFSET(0)] AS latest_score
  FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_session_wise_qa_rating`
  WHERE UPPER(TRIM(evaluation_type)) = 'SCHEDULE'
  GROUP BY 1, 2
),

-- rubric: one value per section + criterion (averaged if a section was evaluated more than once)
rub AS (
  SELECT
    LOWER(REPLACE(TRIM(session_section_id), '-', '')) AS sid,
    MAX(IF(rn = 'curriculum alignment and content accuracy',          sc, NULL)) AS content,
    MAX(IF(rn = 'pedagogical quality',                                 sc, NULL)) AS pedagogy,
    MAX(IF(rn = 'language',                                            sc, NULL)) AS language,
    MAX(IF(rn = 'student engagement',                                  sc, NULL)) AS engagement,
    MAX(IF(rn = 'professional conduct and ethics',                     sc, NULL)) AS conduct,
    MAX(IF(rn = 'doubt resolution and practice session facilitation',  sc, NULL)) AS doubt,
    MAX(IF(rn = 'process adherence',                                   sc, NULL)) AS process,
    MAX(IF(rn = 'clarity and communication',                           sc, NULL)) AS clarity,
    MAX(IF(rn = 'pedagogical support and guidance',                    sc, NULL)) AS support,
    MAX(IF(rn = 'student engagement and independence',                 sc, NULL)) AS independence
  FROM (
    SELECT session_section_id, LOWER(TRIM(name)) AS rn, AVG(score) AS sc
    FROM `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace.niat_session_wise_rubric_metrics_details`
    WHERE score IS NOT NULL
    GROUP BY 1, 2
  )
  GROUP BY 1
),

j AS (
  SELECT
    s.*,
    IFNULL(q.reports, 0)           AS reports,
    IFNULL(q.completed_reports, 0) AS completed_reports,
    q.latest_score,
    r.sid IS NOT NULL              AS has_rubric,
    CASE
      WHEN s.session_type NOT IN ('LECTURE', 'PRACTICE') THEN NULL
      WHEN q.latest_score IS NULL                        THEN NULL
      WHEN q.latest_score = 0 AND r.sid IS NULL          THEN NULL   -- zero without rubric = not a real score
      ELSE q.latest_score
    END AS qa_score,
    r.content, r.pedagogy, r.language, r.engagement, r.conduct,
    r.doubt, r.process, r.clarity, r.support, r.independence
  FROM s
  LEFT JOIN qa_sec q ON q.sid = s.sid AND q.iid = s.iid
  LEFT JOIN rub    r ON r.sid = s.sid
)

SELECT
  session_date,
  campus_id,
  campus_name,
  campus_type,
  subject,
  course_category,
  session_type,
  iid                                              AS instructor_user_id,
  COUNT(*)                                         AS sessions_scheduled,
  COUNTIF(reports > 0)                             AS sessions_with_qa_report,
  COUNTIF(completed_reports > 0)                   AS sessions_with_completed_qa,
  COUNTIF(qa_score IS NOT NULL)                    AS scored,
  ROUND(SUM(qa_score), 4)                          AS score_sum,
  MIN(qa_score)                                    AS min_score,
  COUNTIF(qa_score = 0)                            AS zero_scores,
  COUNTIF(session_type IN ('LECTURE', 'PRACTICE') AND latest_score = 0 AND NOT has_rubric) AS unscored_zero_reports,
  COUNTIF(qa_score IS NOT NULL AND content      IS NOT NULL) AS content_n,      ROUND(SUM(IF(qa_score IS NOT NULL, content,      NULL)), 4) AS content_sum,
  COUNTIF(qa_score IS NOT NULL AND pedagogy     IS NOT NULL) AS pedagogy_n,     ROUND(SUM(IF(qa_score IS NOT NULL, pedagogy,     NULL)), 4) AS pedagogy_sum,
  COUNTIF(qa_score IS NOT NULL AND language     IS NOT NULL) AS language_n,     ROUND(SUM(IF(qa_score IS NOT NULL, language,     NULL)), 4) AS language_sum,
  COUNTIF(qa_score IS NOT NULL AND engagement   IS NOT NULL) AS engagement_n,   ROUND(SUM(IF(qa_score IS NOT NULL, engagement,   NULL)), 4) AS engagement_sum,
  COUNTIF(qa_score IS NOT NULL AND conduct      IS NOT NULL) AS conduct_n,      ROUND(SUM(IF(qa_score IS NOT NULL, conduct,      NULL)), 4) AS conduct_sum,
  COUNTIF(qa_score IS NOT NULL AND doubt        IS NOT NULL) AS doubt_n,        ROUND(SUM(IF(qa_score IS NOT NULL, doubt,        NULL)), 4) AS doubt_sum,
  COUNTIF(qa_score IS NOT NULL AND process      IS NOT NULL) AS process_n,      ROUND(SUM(IF(qa_score IS NOT NULL, process,      NULL)), 4) AS process_sum,
  COUNTIF(qa_score IS NOT NULL AND clarity      IS NOT NULL) AS clarity_n,      ROUND(SUM(IF(qa_score IS NOT NULL, clarity,      NULL)), 4) AS clarity_sum,
  COUNTIF(qa_score IS NOT NULL AND support      IS NOT NULL) AS support_n,      ROUND(SUM(IF(qa_score IS NOT NULL, support,      NULL)), 4) AS support_sum,
  COUNTIF(qa_score IS NOT NULL AND independence IS NOT NULL) AS independence_n, ROUND(SUM(IF(qa_score IS NOT NULL, independence, NULL)), 4) AS independence_sum
FROM j
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
ORDER BY session_date, campus_name, subject, session_type, instructor_user_id;
