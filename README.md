# QA email drafts

Upload the instructor QA export (last 7 days). Each instructor is judged on their
last 3 days with QA reviews; days without reviews are skipped. The app flags
instructors whose daily average is at or below the threshold, shows their scores and rubric categories
(lecture and practice sessions), and writes each instructor's email with an LLM
using their scores and the updated rubric workbook. If the AI isn't set up or
fails, a rule-based draft is always available.

## Files
- `app.py` – Flask web app (routes, password, rubric upload)
- `qa_engine.py` – reads the export, flags instructors, builds the facts and the rule-based draft
- `roster.py` – reads the roster export and maps each instructor to their capability manager emails
- `rubrics.py` – reads the rubric workbook and pulls sub-rubric definitions and score bands
- `llm.py` – writes the email with OpenAI gpt-4o-mini (default) or Claude, and checks the numbers it used
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
   - `OPENAI_API_KEY` – required for AI emails
   - `OPENAI_MODEL` – optional, defaults to `gpt-4o-mini` (fast)
   - `CC_EMAILS` – optional, comma-separated addresses to CC on every email; defaults to Riya
     (`riya.rai@nxtwave.co.in`) and Prudhvi (`meka.prudhvicharan@nxtwave.co.in`). Can also be changed in the page's "Always CC" field
   - `CC_ALLOWED_DOMAINS` – optional, defaults to `nxtwave.co.in,nxtwave.tech`. Capability-manager addresses
     outside these domains are shown on the page but not CC'd
   - `APP_PASSWORD` – optional but recommended; the browser will ask for it (any username)
   - To use Claude instead: `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`; `ANTHROPIC_MODEL` is optional and defaults to `claude-haiku-5-5`
5. Deploy and open the URL.

## Run locally
```
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...
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
