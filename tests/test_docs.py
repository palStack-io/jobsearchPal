"""Resume builder: outline the base resume, then build tailored docs from a short JSON spec."""
import datetime as dt

import docx
import pytest

from autoapply import docs


def _para(d, text, bold=None, style=None):
    p = d.add_paragraph(style=style)
    p.add_run(text).bold = bold
    return p


def _two_run(d, label, text):
    p = d.add_paragraph()
    p.add_run(f"{label}: ").bold = True
    p.add_run(text)


def _role(d, title, dates, bold=True):
    p = d.add_paragraph()
    p.add_run(title).bold = bold
    if dates:
        p.add_run("\t").bold = bold
        p.add_run(dates).bold = bold
    return p


@pytest.fixture
def base(tmp_path):
    """Same shape as a real base resume: name, contact, headline, summary, skills, roles with groups, education."""
    d = docx.Document()
    _para(d, "Pat Example", bold=True)
    _para(d, "Boston, MA | linkedin.com/in/pat")
    _para(d, "DATA ENGINEER · ANALYTICS", bold=True)
    _para(d, "Original summary.")
    _para(d, "CORE SKILLS", bold=True)
    _two_run(d, "Platforms", "Snowflake, dbt")
    _two_run(d, "Languages", "SQL, Python")
    _two_run(d, "BI", "Power BI")
    _para(d, "PROFESSIONAL EXPERIENCE", bold=True)
    _role(d, "Lead Data Scientist | Big University", "2020 – 2026")
    _role(d, "Data Platform", None, bold=False)
    for b in ("Built the platform.", "Set standards.", "Automated releases."):
        _para(d, b, style="List Bullet")
    _role(d, "Data Science", None, bold=False)
    for b in ("Led analytics.", "Built a model."):
        _para(d, b, style="List Bullet")
    _role(d, "Founder | palStack.io", "2024 – Present")
    _para(d, "Founded a company.", style="List Bullet")
    _para(d, "EDUCATION", bold=True)
    _para(d, "M.S., Computer Science (2017)")
    path = tmp_path / "Example Resume - Base.docx"
    d.save(path)
    return path


def texts(path):
    return [p.text for p in docx.Document(path).paragraphs]


def test_outline_reads_headline_summary_skills_and_experience(base):
    o = docs.outline(base)
    assert o["headline"] == "DATA ENGINEER · ANALYTICS"
    assert o["summary"] == "Original summary."
    assert o["skills"] == [{"label": "Platforms", "text": "Snowflake, dbt"},
                           {"label": "Languages", "text": "SQL, Python"},
                           {"label": "BI", "text": "Power BI"}]
    assert [(b["heading"], b["kind"], len(b["bullets"])) for b in o["experience"]] == [
        ("Lead Data Scientist | Big University", "role", 0),
        ("Data Platform", "group", 3),
        ("Data Science", "group", 2),
        ("Founder | palStack.io", "role", 1),
    ]


def test_build_rewrites_content_and_keeps_roles_dates_and_education(base, tmp_path):
    spec = {"company": "Acme", "resume": {
        "headline": "ANALYTICS ENGINEER · SEMANTIC LAYER",
        "summary": "New summary.",
        "skills": [{"label": "Languages", "text": "SQL (advanced), Python"},
                   {"label": "Semantic Layer", "text": "Snowflake semantic views"}],
        "experience": {"Data Platform": ["Set standards first.", "Built the platform.", "Added a grain bullet."],
                       "Data Science": [],
                       "founder": ["Founded a company; manage a team of 4."]}}}
    r = docs.build(base, spec, tmp_path / "out", last_name="Example", pdf=False)
    t = texts(r["resume"])
    assert r["resume"].endswith("Example Resume - Acme.docx")
    assert t[2] == "ANALYTICS ENGINEER · SEMANTIC LAYER" and t[3] == "New summary."
    i = t.index("CORE SKILLS")
    assert t[i + 1:i + 3] == ["Languages: SQL (advanced), Python", "Semantic Layer: Snowflake semantic views"]
    assert "Platforms: Snowflake, dbt" not in t
    j = t.index("Data Platform")
    assert t[j + 1:j + 4] == ["Set standards first.", "Built the platform.", "Added a grain bullet."]
    assert "Automated releases." not in t
    # An empty list drops a group and its bullets; role and date lines stay.
    assert "Data Science" not in t and "Led analytics." not in t
    assert "Lead Data Scientist | Big University\t2020 – 2026" in t
    assert t[t.index("Founder | palStack.io\t2024 – Present") + 1] == "Founded a company; manage a team of 4."
    assert t[-2:] == ["EDUCATION", "M.S., Computer Science (2017)"]
    d = docx.Document(r["resume"])
    new = next(p for p in d.paragraphs if p.text == "Added a grain bullet.")
    assert new.style.name == "List Bullet" and not new.runs[0].bold
    skill = next(p for p in d.paragraphs if p.text.startswith("Semantic Layer"))
    assert skill.runs[0].bold and not skill.runs[1].bold


def test_left_out_parts_stay_as_in_the_base(base, tmp_path):
    r = docs.build(base, {"company": "Acme", "resume": {"summary": "Only this."}}, tmp_path, last_name="Example", pdf=False)
    t = texts(r["resume"])
    assert t[2] == "DATA ENGINEER · ANALYTICS" and t[3] == "Only this."
    assert "Automated releases." in t and "BI: Power BI" in t


def test_unknown_or_ambiguous_section_is_an_error_naming_the_choices(base, tmp_path):
    with pytest.raises(docs.SpecError, match="Data Platform"):
        docs.build(base, {"company": "A", "resume": {"experience": {"Nope": ["x"]}}}, tmp_path, last_name="E", pdf=False)
    with pytest.raises(docs.SpecError, match="more than one"):
        docs.build(base, {"company": "A", "resume": {"experience": {"Data": ["x"]}}}, tmp_path, last_name="E", pdf=False)
    with pytest.raises(docs.SpecError, match="company"):
        docs.build(base, {"resume": {}}, tmp_path, last_name="E", pdf=False)


def test_cover_letter_keeps_header_and_writes_letter(base, tmp_path):
    spec = {"company": "Acme", "cover_letter": {"to": "Hiring Team, Data", "greeting": "Dear Hiring Team,",
                                                "paragraphs": ["First.", "Second."]}}
    r = docs.build(base, spec, tmp_path, last_name="Example", pdf=False, today=dt.date(2026, 11, 6))
    assert r["cover_letter"].endswith("Example Cover Letter - Acme.docx")
    assert texts(r["cover_letter"]) == ["Pat Example", "Boston, MA | linkedin.com/in/pat", "November 6, 2026",
                                        "Hiring Team, Data\nAcme", "Dear Hiring Team,", "First.", "Second.",
                                        "Sincerely,\nPat Example"]
    assert "resume" in r  # the resume is always built, as the base when the spec leaves it out


def test_resume_over_two_pages_is_reported(base, tmp_path, monkeypatch):
    monkeypatch.setattr(docs, "to_pdf", lambda path: path.with_suffix(".pdf"))
    monkeypatch.setattr(docs, "pdf_pages", lambda path: 3 if "Resume" in path.name else 1)
    r = docs.build(base, {"company": "Acme", "cover_letter": {"paragraphs": ["Hi."]}}, tmp_path, last_name="E")
    assert r["pages"] == {"resume": 3, "cover_letter": 1}
    assert any("Resume is 3 pages" in p and "Data Platform" in p for p in r["problems"])


def test_missing_libreoffice_is_reported_not_raised(base, tmp_path, monkeypatch):
    monkeypatch.setattr(docs, "to_pdf", lambda path: None)
    r = docs.build(base, {"company": "Acme"}, tmp_path, last_name="E")
    assert any("PDF" in p for p in r["problems"])


# ---------- impact figures and the look ----------

def _runs(path, startswith):
    p = next(p for p in docx.Document(path).paragraphs if p.text.startswith(startswith))
    return [(r.text, bool(r.bold), str(r.font.color.rgb) if r.font.color.type else None) for r in p.runs if r.text]


def test_starred_figures_become_bold_accent_runs(base, tmp_path):
    spec = {"company": "Acme", "resume": {
        "summary": "Data lead with **10+ years** of work.",
        "experience": {"Data Platform": ["Cut audits from **3 days to 2 hours** across **12** teams.", "No figure here."]}}}
    r = docs.build(base, spec, tmp_path, last_name="E", pdf=False)
    assert _runs(r["resume"], "Cut audits") == [
        ("Cut audits from ", False, None), ("3 days to 2 hours", True, docs.ACCENT), (" across ", False, None),
        ("12", True, docs.ACCENT), (" teams.", False, None)]
    assert _runs(r["resume"], "Data lead")[1] == ("10+ years", True, docs.ACCENT)
    assert "No figure here." in texts(r["resume"])
    assert not any("**" in t for t in texts(r["resume"]))


def test_outline_round_trips_bold_figures(base, tmp_path):
    spec = {"company": "Acme", "resume": {"experience": {"Data Platform": ["Built **99 models** for **2M rows**."]}}}
    r = docs.build(base, spec, tmp_path, last_name="E", pdf=False)
    o = docs.outline(r["resume"])
    assert o["experience"][1]["bullets"] == ["Built **99 models** for **2M rows**."]


def test_unpaired_stars_are_dropped(base, tmp_path):
    spec = {"company": "Acme", "resume": {"experience": {"Data Platform": ["Built **99 models."]}}}
    r = docs.build(base, spec, tmp_path, last_name="E", pdf=False)
    assert "Built 99 models." in texts(r["resume"])


def test_theme_colours_name_headings_roles_and_rules_headings(base, tmp_path):
    r = docs.build(base, {"company": "Acme"}, tmp_path, last_name="E", pdf=False)
    d = docx.Document(r["resume"])
    by = {p.text: p for p in d.paragraphs}
    name = by["Pat Example"].runs[0]
    assert str(name.font.color.rgb) == docs.ACCENT and name.font.size.pt == docs.NAME_PT
    assert str(by["Boston, MA | linkedin.com/in/pat"].runs[0].font.color.rgb) == str(docs.MUTED)
    assert str(by["CORE SKILLS"].runs[0].font.color.rgb) == docs.ACCENT
    assert by["CORE SKILLS"]._p.pPr.find(docs.qn("w:pBdr")) is not None
    role = by["Lead Data Scientist | Big University\t2020 – 2026"]
    assert str(role.runs[0].font.color.rgb) == docs.ACCENT
    # building twice (a themed base) adds no second rule
    again = docs.build(r["resume"], {"company": "Beta"}, tmp_path / "2", last_name="E", pdf=False)
    head = next(p for p in docx.Document(again["resume"]).paragraphs if p.text == "CORE SKILLS")
    assert len(head._p.pPr.find(docs.qn("w:pBdr"))) == 1


def test_classic_keeps_the_base_look_and_custom_hex_works(base, tmp_path):
    plain = docs.build(base, {"company": "Acme"}, tmp_path / "a", last_name="E", pdf=False, look="classic")
    assert docx.Document(plain["resume"]).paragraphs[0].runs[0].font.color.type is None
    teal = docs.build(base, {"company": "Acme"}, tmp_path / "b", last_name="E", pdf=False, accent="#0f766e")
    assert str(docx.Document(teal["resume"]).paragraphs[0].runs[0].font.color.rgb) == "0F766E"
    with pytest.raises(docs.SpecError, match="hex"):
        docs.build(base, {"company": "Acme"}, tmp_path / "c", last_name="E", pdf=False, accent="blue")


def test_cover_letter_gets_the_letterhead(base, tmp_path):
    spec = {"company": "Acme", "cover_letter": {"paragraphs": ["Hello."]}}
    r = docs.build(base, spec, tmp_path, last_name="E", pdf=False)
    ps = docx.Document(r["cover_letter"]).paragraphs
    assert str(ps[0].runs[0].font.color.rgb) == docs.ACCENT
    assert ps[1]._p.pPr.find(docs.qn("w:pBdr")) is not None
    assert "Hello." in [p.text for p in ps]


def test_teal_band_shades_the_headline_and_bars_the_headings(base, tmp_path):
    r = docs.build(base, {"company": "Acme"}, tmp_path, last_name="E", pdf=False, look="teal")
    by = {p.text: p for p in docx.Document(r["resume"]).paragraphs}
    head = by["DATA ENGINEER · ANALYTICS"]
    assert head._p.pPr.find(docs.qn("w:shd")).get(docs.qn("w:fill")) == "0F766E"
    assert str(head.runs[0].font.color.rgb) == "FFFFFF"
    sec = by["CORE SKILLS"]._p.pPr
    assert sec.find(docs.qn("w:pBdr")).find(docs.qn("w:left")) is not None
    with pytest.raises(docs.SpecError, match="navy"):
        docs.build(base, {"company": "Acme"}, tmp_path / "x", last_name="E", pdf=False, look="neon")


@pytest.mark.parametrize("look", list(docs.LOOKS))
def test_every_look_keeps_plain_text_for_resume_scanners(base, tmp_path, look):
    """Applicant tracking systems read the text: no tables, text boxes or columns, and the words come out in order."""
    spec = {"company": "Acme", "resume": {"experience": {"Data Platform": ["Cut audits by **80%** in **2** terms."]}}}
    r = docs.build(base, spec, tmp_path, last_name="E", pdf=False, look=look)
    d = docx.Document(r["resume"])
    body = d.element.body
    assert not d.tables
    assert not body.findall(".//" + docs.qn("w:txbxContent")) and not body.findall(".//" + docs.qn("w:cols"))[1:]
    t = texts(r["resume"])
    assert t[:3] == ["Pat Example", "Boston, MA | linkedin.com/in/pat", "DATA ENGINEER · ANALYTICS"]
    assert "Cut audits by 80% in 2 terms." in t
    assert {"CORE SKILLS", "PROFESSIONAL EXPERIENCE", "EDUCATION"} <= set(t)


def test_a_resume_slightly_over_two_pages_is_tightened_to_fit(base, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(docs, "to_pdf", lambda path: path.with_suffix(".pdf"))

    def pages(path):
        calls.append(path.name)
        return 3 if len(calls) < 3 else 2  # over at first and after step 1, fits after step 2
    monkeypatch.setattr(docs, "pdf_pages", pages)
    r = docs.build(base, {"company": "Acme"}, tmp_path, last_name="E")
    assert r["pages"]["resume"] == 2 and not r["problems"]
    assert "step 2" in r["fit"]
    d = docx.Document(r["resume"])
    assert d.sections[0].left_margin.inches == pytest.approx(0.5, abs=0.01)
    assert texts(r["resume"])[0] == "Pat Example"  # the words are untouched


def test_type_never_shrinks_below_the_floor(base, tmp_path):
    d = docx.Document(base)
    d.styles["Normal"].font.size = docs.Pt(10)
    docs._tighten(d, 0.5, 0.4, 1.0)
    assert d.styles["Normal"].font.size.pt == docs.MIN_BODY_PT
