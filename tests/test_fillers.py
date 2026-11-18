"""Runs the filler against local copies of Greenhouse/Lever/Ashby-style forms."""
import functools
import http.server
import threading
from pathlib import Path

import pytest
import yaml

from autoapply.fillers import fill, scan, submit

HERE = Path(__file__).parent
PROFILE = yaml.safe_load((HERE.parent / "profile.example.yaml").read_text())


@pytest.fixture(scope="module")
def base_url():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(HERE / "fixtures"))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def pdfs(tmp_path):
    for n in ("resume.pdf", "cover.pdf"):
        (tmp_path / n).write_bytes(b"%PDF-1.4\n%test\n")
    return {"resume": str(tmp_path / "resume.pdf"), "cover_letter": str(tmp_path / "cover.pdf")}


def labels(items):
    return {i["label"] for i in items}


def test_greenhouse_fills_profile_and_files_but_holds_consent(base_url, pdfs, tmp_path):
    plan = {"url": f"{base_url}/gh/", "ats": "greenhouse", "files": pdfs,
            "answers": {"Why do you want to work here?": "Semantic layers."}}
    r = fill(plan, PROFILE, tmp_path)
    filled = {f["label"]: f["value"] for f in r.filled}
    assert filled["Resume/CV"] == "resume.pdf"
    assert filled["Cover Letter"] == "cover.pdf"
    assert filled["Will you now or in the future require visa sponsorship?"] == "No"
    assert filled["Are you legally authorized to work in the United States?"] == "Yes"
    assert {"Gender"} <= labels(r.held)
    assert any("Privacy Notice" in b["label"] for b in r.blocking)
    assert Path(r.screenshot).exists()


def test_submit_refused_without_approval(base_url, pdfs, tmp_path):
    with pytest.raises(PermissionError):
        submit({"url": f"{base_url}/gh/", "ats": "greenhouse", "files": pdfs}, PROFILE, tmp_path)


def test_submit_blocked_while_consent_open(base_url, pdfs, tmp_path):
    plan = {"url": f"{base_url}/gh/", "ats": "greenhouse", "files": pdfs, "approved_by_user": True}
    r = submit(plan, PROFILE, tmp_path)
    assert not r.submitted
    assert any("required" in e["error"] for e in r.errors)


def test_submit_after_user_approves_consent(base_url, pdfs, tmp_path):
    plan = {"url": f"{base_url}/gh/", "ats": "greenhouse", "files": pdfs, "approved_by_user": True,
            "approved": {"I acknowledge the Privacy Notice": "yes", "Gender": "Decline to self-identify"}}
    r = submit(plan, PROFILE, tmp_path)
    assert r.submitted, r.errors
    assert "thank you" in r.confirmation.lower()


def test_lever_required_marks_and_checkbox_group(base_url, pdfs, tmp_path):
    fields = {f["label"]: f for f in scan(f"{base_url}/lever", "lever")}
    assert fields["Full name"]["required"]
    assert fields["Which of these tools have you used?"]["options"] == ["dbt", "Snowflake", "Databricks"]
    plan = {"url": f"{base_url}/lever", "ats": "lever", "files": pdfs,
            "answers": {"Which of these tools have you used?": ["dbt", "Snowflake"]}}
    r = fill(plan, PROFILE, tmp_path)
    assert not r.errors and not r.unanswered
    assert "Resume/CV" in labels(r.filled)


def test_ashby_hidden_file_and_combobox(base_url, pdfs, tmp_path):
    plan = {"url": f"{base_url}/ashby", "ats": "ashby", "files": pdfs,
            "answers": {"Years of experience with dbt": "5"}}
    r = fill(plan, PROFILE, tmp_path)
    assert not r.errors, r.errors
    assert {"Resume", "Location", "Years of experience with dbt"} <= labels(r.filled)


def test_greenhouse_react_selects(base_url, pdfs, tmp_path):
    fields = scan(f"{base_url}/gh-react/", "greenhouse")
    by = {f["label"]: f for f in fields}
    assert len(fields) == 12, [f["label"] for f in fields]  # no hidden mirror duplicates
    assert by["Resume/CV"]["kind"] == "file" and by["Cover Letter"]["kind"] == "file"
    assert by["Years of data engineering experience"]["options"] == ["0-2 years", "3-5 years", "6-8 years", "8+ years"]
    assert by["Location (City)"].get("autocomplete")

    plan = {"url": f"{base_url}/gh-react/", "ats": "greenhouse", "files": pdfs,
            "answers": {"Years of data engineering experience": "8+ years", "Location (City)": "Springfield, Illinois",
                        "How did you hear about us?": "Built In", "Country": "United States",
                        "Do you accept the listed salary range?": "Yes"}}
    r = fill(plan, PROFILE, tmp_path)
    assert not r.errors, r.errors
    filled = {f["label"]: (f["value"], f["source"]) for f in r.filled}
    assert filled["Will you require sponsorship?"] == ("No", "profile")
    # The phone-country list stays on the page; each dropdown must read only its own menu.
    assert filled["How did you hear about us?"] == ("Built In", "plan")
    # "relocation" is not "location"; a yes/no salary question doesn't get a number.
    assert filled["Are you open to relocation?"] == ("No", "profile")
    assert filled["Do you accept the listed salary range?"] == ("Yes", "plan")
    # A short key ("Country") must not answer a long question that mentions the word.
    assert filled["Are you legally authorized to work in the country in which this job is based?"] == ("Yes", "profile")
    filled = {k: v[0] for k, v in filled.items()}
    assert filled["Resume/CV"] == "resume.pdf" and filled["Cover Letter"] == "cover.pdf"


def test_submit_reports_validation_errors(base_url, pdfs, tmp_path):
    r = submit({"url": f"{base_url}/gh-err/", "ats": "greenhouse", "approved_by_user": True}, PROFILE, tmp_path)
    assert not r.submitted and r.submit_clicked
    assert any("Phone must be a valid number" in e["error"] for e in r.errors)


def test_submit_confirmed_by_redirect(base_url, pdfs, tmp_path):
    r = submit({"url": f"{base_url}/gh-redirect/", "ats": "greenhouse", "approved_by_user": True}, PROFILE, tmp_path)
    assert r.submitted, r.errors


def test_standing_answers_prefill_held_questions_until_reviewed(base_url, pdfs, tmp_path):
    profile = {**PROFILE, "standing_answers": [
        {"match": "consent|privacy|acknowledge", "value": "yes"},
        {"match": "gender", "value": "Decline to self-identify"}]}
    plan = {"url": f"{base_url}/gh/", "ats": "greenhouse", "files": pdfs}
    r = fill(plan, profile, tmp_path)
    held = {h["label"]: h for h in r.held}
    assert held["Gender"]["value"] == "Decline to self-identify" and held["Gender"]["prefilled"]
    consent = next(h for k, h in held.items() if "Privacy Notice" in k)
    assert consent["value"] == "yes" and consent["prefilled"]
    assert not any("Privacy" in b["label"] for b in r.blocking)  # pre-filled, so not blocking
    assert not any(f["label"] == "Gender" for f in r.filled)  # shown in the check box, not as a normal answer
    # Once you've reviewed and cleared it, a blank stays blank.
    r2 = fill({**plan, "held_reviewed": True, "approved": {"Gender": "Female"}}, profile, tmp_path)
    held2 = {h["label"]: h for h in r2.held}
    assert held2["Gender"]["value"] == "Female" and not held2["Gender"].get("prefilled")
    assert not next(h for k, h in held2.items() if "Privacy Notice" in k).get("value")


def test_setup_saves_standing_answers_and_pronunciation(tmp_path):
    import shutil
    from autoapply import setup
    from autoapply.config import Settings
    for f in ("profile.example.yaml",):
        shutil.copy(HERE.parent / f, tmp_path / f)
    s = Settings(root=tmp_path, raw={"data_dir": str(tmp_path / "data")}, profile={})
    setup.save_profile(s, {"first_name": "Ada", "last_name": "L", "email": "a@x.org", "name_pronunciation": "AY-duh",
                           "standing_form": "1", "stand::consent": "1", "stand::veteran": "I am not a protected veteran",
                           "stand::gender": ""})
    prof = yaml.safe_load((tmp_path / "profile.yaml").read_text())
    assert {r["value"] for r in prof["standing_answers"]} == {"yes", "I am not a protected veteran"}
    assert setup.standing_values(prof) == {"consent": "yes", "veteran": "I am not a protected veteran"}
    from autoapply.fillers.base import resolve
    assert resolve({"label": "Name Pronunciation", "kind": "text"}, {}, prof) == ("AY-duh", "profile")


def test_submit_skips_hidden_captcha_button(base_url, pdfs, tmp_path):
    # Lever's hidden hCaptcha button matched first and the click timed out into a 500.
    r = submit({"url": f"{base_url}/lever-hcaptcha", "ats": "lever", "approved_by_user": True}, PROFILE, tmp_path)
    assert r.submit_clicked and not r.submitted
    assert any("captcha" in e["error"] for e in r.errors)


def test_submit_without_visible_button_reports_instead_of_raising(base_url, pdfs, tmp_path):
    r = submit({"url": f"{base_url}/no-submit/", "ats": "generic", "approved_by_user": True}, PROFILE, tmp_path)
    assert not r.submit_clicked and not r.submitted
    assert any("Submit button" in e["error"] for e in r.errors)


def test_same_label_choice_question_does_not_take_the_phone_number():
    # Ashby (Instructure): a "Phone" text field and a "Phone" text-message consent radio on one form.
    from autoapply.fillers.base import resolve, norm
    prof = {"phone": "(978) 555-0100", "rules": [{"match": "phone", "key": "phone"}]}
    consent = {"label": "Phone", "kind": "radio",
               "options": [{"text": "Yes - I consent to receiving text messages"}, {"text": "No - I do not consent to receiving text messages"}]}
    text = {"label": "Phone", "kind": "text"}
    assert resolve(consent, {}, prof) == (None, "")  # left for the person, not a fill error
    assert resolve(text, {}, prof) == ("(978) 555-0100", "profile")
    # The person picks a consent answer under "Phone": it goes to the radio, never into the number field.
    plan = {"answers": {"Phone": "No - I do not consent to receiving text messages"}}
    sib = {norm(o["text"]) for o in consent["options"]}
    assert resolve(consent, plan, prof, sib)[1] == "plan"
    assert resolve(text, plan, prof, sib) == ("(978) 555-0100", "profile")


def test_handoff_with_browser_keeps_browser_open(base_url, pdfs):
    """Finish here hands handoff a browser it did not launch: only its own context is closed afterwards."""
    from playwright.sync_api import sync_playwright
    from autoapply.fillers.base import handoff
    with sync_playwright() as p:
        b = p.chromium.launch()
        handoff({"url": f"{base_url}/gh/", "ats": "greenhouse", "files": pdfs}, PROFILE, browser=b, tick=lambda: False)
        assert b.is_connected() and len(b.contexts) == 0
        b.close()


def test_handoff_on_page_called_after_fill(base_url, pdfs):
    from playwright.sync_api import sync_playwright
    from autoapply.fillers.base import handoff
    seen = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        handoff({"url": f"{base_url}/gh/", "ats": "greenhouse", "files": pdfs}, PROFILE, browser=b, tick=lambda: False,
                on_page=lambda pg, rep: seen.append((pg.input_value("#first_name"), type(rep).__name__)))
        b.close()
    assert seen == [("Jane", "Report")]


# ---- a posting the company took down ----

CLOSED = "This posting is closed"


def test_removed_ashby_job_says_closed_not_submit_button(base_url, pdfs, tmp_path):
    """Optro on Ashby (2026-11-17): the page said "Job not found", the filler said it couldn't find a Submit button."""
    r = submit({"url": f"{base_url}/ashby-closed", "ats": "ashby", "approved_by_user": True, "files": pdfs},
               PROFILE, tmp_path)
    assert r.closed and not r.submit_clicked and not r.filled
    assert [e["label"] for e in r.errors] == ["(posting)"] and CLOSED in r.errors[0]["error"]
    assert r.screenshot and Path(r.screenshot).exists()


def test_greenhouse_no_longer_open_is_closed(base_url, pdfs, tmp_path):
    r = fill({"url": f"{base_url}/gh-closed/", "ats": "greenhouse", "files": pdfs}, PROFILE, tmp_path)
    assert r.closed and CLOSED in r.errors[0]["error"]


def test_missing_page_is_closed(base_url, pdfs, tmp_path):
    r = fill({"url": f"{base_url}/gone-for-good/", "ats": "generic", "files": pdfs}, PROFILE, tmp_path)
    assert r.closed


def test_open_form_mentioning_no_longer_available_is_not_closed():
    """Text alone isn't enough: a live form with a Submit button stays open whatever its description says."""
    from playwright.sync_api import sync_playwright
    from autoapply.fillers.base import closed_reason, GENERIC
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.set_content("<p>Our old office is no longer available.</p><form><input name=a><button type=submit>Apply</button></form>")
        assert closed_reason(pg, GENERIC, 200) == ""
        pg.set_content("<p>Our old office is no longer available.</p><form><input name=a></form>")
        assert closed_reason(pg, GENERIC, 200) == ""  # no closed wording that names the job, no form button: not ours to call
        b.close()


def test_handoff_on_closed_posting_skips_the_fill(base_url, pdfs):
    from playwright.sync_api import sync_playwright
    from autoapply.fillers.base import handoff
    seen = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        r = handoff({"url": f"{base_url}/ashby-closed", "ats": "ashby", "files": pdfs}, PROFILE, browser=b,
                    tick=lambda: False, on_page=lambda pg, rep: seen.append(rep.closed))
        b.close()
    assert r.closed and seen == [True] and not r.filled
