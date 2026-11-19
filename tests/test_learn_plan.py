"""Skills: a learning plan for up to three picked skills (11/18). One call, saved; links from the checked list only."""
import json
from datetime import datetime

from autoapply import coach, learn
from web_client import client
from test_daily import make

REPLY = json.dumps({"weeks": [
    {"title": "dbt on data you know", "body": "Do the free course, then rebuild a report as models.", "skill": "dbt"},
    {"title": "Tests and metrics", "body": "Add tests and two metrics.", "skill": "dbt"},
    {"title": "Run it nightly", "body": "One Airflow DAG on your laptop.", "skill": "Airflow"},
    {"title": "Look at it", "body": "Model it in LookML.", "skill": "https://evil.example/course"}],
    "show": "A public repo with the models and the DAG."})


def test_make_plan_saves_and_links_only_from_the_list(tmp_path):
    s, db, cfg = make(tmp_path)
    seen = []
    out = learn.make_plan(db, s, ["dbt", "Airflow", "dbt", "Spark", "Kafka / streaming"],
                          ask=lambda p: (seen.append(p) or REPLY, {"model": "test", "cost": 0.0, "tokens": 900}),
                          now=datetime(2026, 11, 18, 9))
    assert out["status"] == "ready" and out["skills"] == ["dbt", "Airflow", "Spark"]  # deduped, three at most
    assert "data, not instructions" in seen[0] and "Analytics engineer" in seen[0]  # what you know goes in, fenced
    assert out["weeks"][0]["link"] == "https://courses.getdbt.com/" and out["weeks"][2]["link"].startswith("https://airflow")
    assert out["weeks"][3]["link"] == "" and "evil" not in json.dumps(out)  # a model-made link never survives
    assert coach.note(db, learn.PLAN_KEY)["weeks"][0]["title"] == "dbt on data you know"
    assert db.conn.execute("select count(*) from usage where source = 'learn-plan'").fetchone()[0] == 1


def test_failed_reply_is_kind_and_kept(tmp_path):
    s, db, cfg = make(tmp_path)
    out = learn.make_plan(db, s, ["dbt"], ask=lambda p: ("no json here", {"model": "t"}))
    assert out["status"] == "failed"


def test_skills_page_pick_and_start(tmp_path, monkeypatch):
    s, db, cfg = make(tmp_path)
    monkeypatch.setattr(learn, "start_plan", lambda d, st, picks: learn.make_plan(
        d, st, picks, ask=lambda p: (REPLY, {"model": "t"})))
    c = client(s, db)
    html = c.get("/skills").text
    assert 'action="/skills/plan"' in html and "Make a plan" in html
    assert c.post("/skills/plan", data={}).status_code == 400
    c.post("/skills/plan", data={"picks": ["dbt", "Airflow"]})
    html = c.get("/skills").text
    assert "Week 4" in html and "Start this plan" in html and "courses.getdbt.com" in html
    c.post("/skills/plan/start")
    assert "dbt" in learn.marks(s.data_dir)["learning"]
    assert "Start this plan" not in c.get("/skills").text
