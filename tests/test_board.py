"""The Leads board (11/18): stars put leads on it, cards move and tick steps, I applied takes a card off."""
from datetime import datetime

from autoapply import board, leads
from web_client import client
from test_daily import make
from test_leads import LINKEDIN


def seeded(tmp_path):
    s, db, cfg = make(tmp_path)
    leads.save(db, leads.parse(LINKEDIN), "Linkedin", datetime(2026, 11, 3, 9), {}, set())
    return s, db, leads.key("Initech", "Staff Data Engineer, Platform"), leads.key("Hooli", "Senior Analytics Engineer")


def test_star_adds_unstar_removes(tmp_path):
    s, db, k, k2 = seeded(tmp_path)
    assert board.cards(db) == []
    leads.set_status(db, k, "starred")  # a star from anywhere (older stars too) shows up on the board
    cs = board.cards(db)
    assert [c["key"] for c in cs] == [k] and cs[0]["col"] == 0 and cs[0]["n_done"] == 0
    board.remove(db, k)
    assert board.cards(db) == [] and leads.get(db, k)["status"] == "new"


def test_move_steps_save(tmp_path):
    s, db, k, k2 = seeded(tmp_path)
    board.add(db, k)
    board.move(db, k, 2)
    board.move(db, k, 9)  # clamped to the last column
    board.toggle_step(db, k, 0)
    board.toggle_step(db, k, 2)
    board.toggle_step(db, k, 2)
    board.save(db, k, "Message Sam", "2026-11-19", "Sam worked with me")
    c = board.cards(db)[0]
    assert c["col"] == 3 and c["done"] == [True, False, False, False, False] and c["next_step"] == "Message Sam"
    cols = board.columns(db)
    assert [x["name"] for x in cols] == ["Shortlist", "Research", "Reach out", "Ready to apply"]
    assert cols[3]["cards"][0]["key"] == k


def test_applied_or_dismissed_leaves(tmp_path):
    s, db, k, k2 = seeded(tmp_path)
    board.add(db, k)
    board.add(db, k2)
    db.conn.execute("update leads set status = 'applied' where key = ?", (k,))
    db.conn.commit()
    assert [c["key"] for c in board.cards(db)] == [k2]


def test_board_page_and_actions(tmp_path):
    s, db, k, k2 = seeded(tmp_path)
    c = client(s, db)
    c.post("/leads", data={"key": k, "status": "starred"})
    html = c.get("/leads?view=board").text
    assert "Shortlist" in html and "Ready to apply" in html and "Staff Data Engineer, Platform" in html
    c.post("/board/move", data={"key": k, "col": "1"})
    c.post("/board/step", data={"key": k, "i": "1"})
    c.post("/board/save", data={"key": k, "next_step": "Find who leads data", "due": "2026-11-20", "notes": "dbt shop"})
    card = board.cards(db)[0]
    assert card["col"] == 1 and card["done"][1] and card["notes"] == "dbt shop"
    c.post("/board/lookup", data={"key": k})
    assert leads.get(db, k)["want_lookup"] == 1
    r = c.post("/board/applied", data={"key": k}, follow_redirects=False)
    assert r.status_code == 303
    assert board.cards(db) == [] and leads.get(db, k)["status"] == "applied"
    assert "Staff Data Engineer, Platform" in c.get("/leads?view=applied").text
    c.post("/board/add", data={"key": k2})
    c.post("/board/remove", data={"key": k2})
    assert board.cards(db) == [] and leads.get(db, k2)["status"] == "new"


def test_add_job_from_shortlist(tmp_path):
    s, db, k, k2 = seeded(tmp_path)
    key = board.add_job(db, db.get("gh:a:1"))
    assert board.cards(db)[0]["key"] == key and leads.get(db, key)["job_id"] == "gh:a:1"
