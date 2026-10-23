# QA email drafts

Upload the 3-day instructor QA export. The app flags instructors whose daily
average is at or below the threshold, shows their scores and rubric categories
(lecture and practice sessions), and writes each instructor's email with an LLM
using their scores and the updated rubric workbook. If the AI isn't set up or
fails, a rule-based draft is always available.

## Files
- `app.py` – Flask web app (routes, password, rubric upload)
- `qa_engine.py` – reads the export, flags instructors, builds the facts and the rule-based draft
- `rubrics.py` – reads the rubric workbook and pulls sub-rubric definitions and score bands
- `llm.py` – writes the email with Claude (default) or OpenAI, and checks the numbers it used
- `templates/index.html` – the page
- `rubrics/Updated_Rubrics_and_Scoring.xlsx` – the rubric workbook the AI uses

## Deploy on Render (Web Service)
1. Push this folder to a GitHub repository (keep the folder structure).
2. In Render choose **New → Web Service** and connect the repository
   (or **New → Blueprint** to use `render.yaml`).
3. Settings:
   - Runtime: Python 3
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app --workers 2 --threads 4 --timeout 180 --bind 0.0.0.0:$PORT`
4. Environment variables:
   - `ANTHROPIC_API_KEY` – required for AI emails
   - `ANTHROPIC_MODEL` – optional, defaults to `claude-haiku-5-5` (fast); set `claude-sonnet-5-5` for more polished writing
   - `APP_PASSWORD` – optional but recommended; the browser will ask for it (any username)
   - To use OpenAI instead: `LLM_PROVIDER=openai` and `OPENAI_API_KEY`; `OPENAI_MODEL` is optional and defaults to `gpt-4o-mini`
5. Deploy and open the URL.

## Run locally
```
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python app.py          # http://localhost:5000
```

## Notes
- A rubric workbook uploaded from the page replaces the bundled one only until
  the service restarts or redeploys. To change it permanently, replace the file
  in `rubrics/` and push.
- Uploaded exports are processed in memory and not stored. When you write an
  email, that instructor's scores (and rubric text) are sent to the LLM provider.
- Every decimal in an AI draft is checked against the data; anything that
  doesn't match is shown as a warning above the email.
