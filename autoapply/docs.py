"""Tailored resume and cover letter from a short JSON spec, so the agent writes content, not python-docx code.

`outline(base)` reads the base resume into the shape the spec uses. `build(base, spec, out_dir)` copies the base,
swaps in the spec's headline, summary, skills and bullets (styles kept, role/date/education lines untouched),
writes a cover letter on the same header, converts both to PDF and reports the page counts.

Spec (data/applications/<slug>/docs.json); every part but "company" is optional and defaults to the base:
  {"company": "Acme",
   "resume": {"headline": "...", "summary": "...",
              "skills": [{"label": "Languages", "text": "SQL, Python"}, ...],      # the full list, in order
              "experience": {"<heading from the outline>": ["bullet", ...], ...}},  # each list replaces that block's
                                                                                   # bullets; [] drops a group
   "cover_letter": {"to": "Hiring Team", "greeting": "Dear Hiring Team,", "paragraphs": ["...", ...],
                    "sign_off": "Sincerely,"}}

In the summary and bullets, **two stars** around a figure ("cut audits from **3 days to 2 hours**") set it in bold
accent, so the numbers that show impact stand out. Both documents also get a little colour: the name, headline,
section headings (with a rule under them), role titles and group lines take the accent, and contact and date lines
go grey. profile.yaml `resume_look` picks one of LOOKS (chosen on the Setup page; navy by default, "classic" keeps
the base resume's own look) and `resume_accent` can swap in another hex colour.
"""
from __future__ import annotations

import copy
import datetime as dt
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import docx
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from docx.text.paragraph import Paragraph
from docx.text.run import Run

MAX_PAGES = {"resume": 2, "cover_letter": 1}
ACCENT = "1F4E79"  # navy: reads as colour on screen and still prints well in greyscale
# The looks a person can pick (Setup page, profile.yaml `resume_look`). Every look keeps one column of real text, so
# applicant tracking systems read it the same way: colour, rules and shading never change the extracted text.
LOOKS = {
    "navy": {"label": "Navy", "accent": "1F4E79", "style": "rule",
             "blurb": "Navy name, headings with a thin rule and role titles; key figures in bold navy. Safe for any field."},
    "teal": {"label": "Teal band", "accent": "0F766E", "style": "band",
             "blurb": "More colour: a teal band behind your headline and tinted section bars. Good for tech and startups."},
    "slate": {"label": "Slate", "accent": "334155", "style": "rule",
              "blurb": "Quiet dark grey with thin rules. For finance, law, government and other conservative fields."},
    "classic": {"label": "My own layout", "accent": None, "style": None,
                "blurb": "Keeps your uploaded resume's own fonts and colours; only bolds the key figures."},
}
DEFAULT_LOOK = "navy"
MUTED = RGBColor(0x55, 0x5F, 0x6B)
NAME_PT = 20
_MARK = re.compile(r"\*\*(.+?)\*\*")


class SpecError(ValueError):
    """The spec asks for something the base resume doesn't have."""


# ---------- reading the base ----------

def _is_bullet(p) -> bool:
    return p.style.name.lower().startswith("list")


def _is_heading(p) -> bool:
    t = p.text.strip()
    return (bool(t) and "\t" not in t and t == t.upper() and any(c.isalpha() for c in t)
            and not _is_bullet(p) and bool(p.runs) and bool(p.runs[0].bold))


class _Resume:
    """Where each part of the base resume is, by paragraph."""

    def __init__(self, d):
        self.d = d
        ps = list(d.paragraphs)
        heads = [i for i, p in enumerate(ps) if _is_heading(p)]
        if len(heads) < 2:
            raise SpecError("The base resume needs an upper-case headline and upper-case section headings.")
        # The first upper-case bold line is the headline; the next non-empty line is the summary.
        self.headline = ps[heads[0]]
        self.summary = next((p for p in ps[heads[0] + 1:heads[1]] if p.text.strip()), None)
        self.sections = {}
        bounds = heads[1:] + [len(ps)]
        for a, b in zip(heads[1:], bounds[1:]):
            self.sections[ps[a].text.strip()] = (ps[a], ps[a + 1:b])
        self.skill_head, self.skills = None, []
        self.blocks = []  # [{"heading", "kind", "para", "bullets": [para]}]
        for name, (head, body) in self.sections.items():
            if "SKILL" in name:
                self.skill_head = head
                self.skills = [p for p in body if len(p.runs) >= 2 and p.runs[0].bold]
            elif "EXPERIENCE" in name:
                for p in body:
                    if not p.text.strip():
                        continue
                    if _is_bullet(p):
                        if self.blocks:
                            self.blocks[-1]["bullets"].append(p)
                    else:
                        self.blocks.append({"heading": p.text.split("\t")[0].strip(),
                                            "kind": "role" if "\t" in p.text else "group",
                                            "para": p, "bullets": []})

    def block(self, key: str) -> dict:
        k = key.strip().lower()
        hits = [b for b in self.blocks if b["heading"].lower() == k]
        if not hits:
            hits = [b for b in self.blocks if b["heading"].lower().startswith(k)]
        if not hits and "|" in k:
            # The base resume's heading was renamed since this text was written ("Founder & Engineer | palStack.io" ->
            # "Founder & Engineering Lead | palStack.io"): the same place and a close title, never a guess between two.
            import difflib
            org = k.rsplit("|", 1)[1].strip()
            same = [b for b in self.blocks if "|" in b["heading"] and b["heading"].lower().rsplit("|", 1)[1].strip() == org]
            title = k.rsplit("|", 1)[0].strip()  # the title alone: a shared place name would inflate the score
            hits = [b for b in same
                    if difflib.SequenceMatcher(None, b["heading"].lower().rsplit("|", 1)[0].strip(), title).ratio() >= 0.75]
        if len(hits) == 1:
            return hits[0]
        names = "; ".join(b["heading"] for b in self.blocks)
        if hits:
            raise SpecError(f'"{key}" matches more than one experience heading. Use one of: {names}')
        raise SpecError(f'No experience heading "{key}". Use one of: {names}')


def _skill(p) -> dict:
    return {"label": p.runs[0].text.strip().rstrip(":").strip(), "text": "".join(r.text for r in p.runs[1:]).strip()}


def _marked(p) -> str:
    """A paragraph's text with its bold runs wrapped in **, the same markup the spec uses."""
    out, bold = [], False
    for r in p.runs:
        if not r.text:
            continue
        b = bool(r.bold) and bool(r.text.strip())
        if b != bold:
            out.append("**")
            bold = b
        out.append(r.text)
    if bold:
        out.append("**")
    return re.sub(r"\*\*(\s*)\*\*", r"\1", "".join(out)).strip()


def outline(base: Path) -> dict:
    r = _Resume(docx.Document(base))
    return {"headline": r.headline.text.strip(),
            "summary": _marked(r.summary) if r.summary else "",
            "skills": [_skill(p) for p in r.skills],
            "experience": [{"heading": b["heading"], "kind": b["kind"], "bullets": [_marked(p) for p in b["bullets"]]}
                           for b in r.blocks]}


# ---------- editing ----------

def _set_text(p, text: str):
    if not p.runs:
        p.add_run(text)
        return
    p.runs[0].text = text
    for r in p.runs[1:]:
        r.text = ""


def _set_rich(p, text: str):
    """Like _set_text, but **x** becomes a bold run (the theme colours it). Unpaired stars are dropped."""
    parts = _MARK.split(text)
    if len(parts) == 1:
        _set_text(p, text.replace("**", ""))
        return
    if not p.runs:
        p.add_run("")
    tpl = p.runs[0]
    for r in p.runs[1:]:
        r._r.getparent().remove(r._r)
    anchor, first = tpl._r, True
    for i, part in enumerate(parts):
        part = part.replace("**", "")
        if not part:
            continue
        if first:
            run, first = tpl, False
        else:
            new = copy.deepcopy(tpl._r)
            anchor.addnext(new)
            run = Run(new, p)
        anchor = run._r
        run.text = part
        run.bold = True if i % 2 else None
        run.font.color.rgb = None
    if first:  # nothing but stars
        tpl.text = ""


def _clone_after(anchor, template, text: str | None = None, rich: bool = False):
    new = copy.deepcopy(template._p)
    anchor._p.addnext(new)
    p = Paragraph(new, anchor._parent)
    if text is not None:
        (_set_rich if rich else _set_text)(p, text)
    return p


# ---------- look ----------

def accent_color(value) -> RGBColor | None:
    """profile.yaml `resume_accent`: a hex colour, or none/off/false for the base resume's own look."""
    if value is None or value == "":
        value = ACCENT
    s = str(value).strip().lstrip("#")
    if s.lower() in ("none", "off", "false", "no"):
        return None
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", s):
        raise SpecError(f'resume_accent "{value}" is not a hex colour like "#1F4E79" (or "none").')
    return RGBColor.from_string(s.upper())


def _rule(p, color: RGBColor):
    """A thin line under the paragraph, once."""
    pPr = p._p.get_or_add_pPr()
    bdr = pPr.find(qn("w:pBdr"))
    if bdr is None:
        bdr = OxmlElement("w:pBdr")
        pPr.append(bdr)
    if bdr.find(qn("w:bottom")) is not None:
        return
    e = OxmlElement("w:bottom")
    for k, v in (("val", "single"), ("sz", "8"), ("space", "1"), ("color", str(color))):
        e.set(qn(f"w:{k}"), v)
    bdr.append(e)


def _color(p, color, only=None):
    for r in p.runs:
        if r.text.strip() and (only is None or only(r)):
            r.font.color.rgb = color


def _header(paras, color: RGBColor):
    """Name in accent at a readable size, the lines under it (contact) grey."""
    lines = [p for p in paras if p.text.strip()]
    if not lines:
        return
    _color(lines[0], color)
    for r in lines[0].runs:
        if r.font.size is None or r.font.size.pt < NAME_PT:
            r.font.size = Pt(NAME_PT)
    for p in lines[1:]:
        _color(p, MUTED)


def _tint(color: RGBColor, keep: float = 0.12) -> str:
    return "".join(f"{round(255 - (255 - c) * keep):02X}" for c in color)


def _shade(p, fill: str):
    pPr = p._p.get_or_add_pPr()
    if pPr.find(qn("w:shd")) is not None:
        return
    e = OxmlElement("w:shd")
    for k, v in (("val", "clear"), ("color", "auto"), ("fill", fill)):
        e.set(qn(f"w:{k}"), v)
    pPr.append(e)


def _bar(p, color: RGBColor):
    """A thick bar at the left of the paragraph, once."""
    pPr = p._p.get_or_add_pPr()
    bdr = pPr.find(qn("w:pBdr"))
    if bdr is None:
        bdr = OxmlElement("w:pBdr")
        pPr.append(bdr)
    if bdr.find(qn("w:left")) is not None:
        return
    e = OxmlElement("w:left")
    for k, v in (("val", "single"), ("sz", "24"), ("space", "4"), ("color", str(color))):
        e.set(qn(f"w:{k}"), v)
    bdr.append(e)


def resolve_look(look=None, accent=None) -> tuple[RGBColor | None, str | None]:
    """(accent colour, style) for a look name plus an optional accent override; "classic" gives (None, None)."""
    key = str(look or DEFAULT_LOOK).strip().lower()
    if key not in LOOKS:
        raise SpecError(f'resume_look "{look}" is not one of: {", ".join(LOOKS)}.')
    spec = LOOKS[key]
    if spec["style"] is None:
        return None, None
    return accent_color(accent if accent not in (None, "") else spec["accent"]), spec["style"]


def _theme(d, color: RGBColor, style: str = "rule"):
    r = _Resume(d)
    ps = list(d.paragraphs)
    _header(ps[:next(i for i, p in enumerate(ps) if p._p is r.headline._p)], color)
    if style == "band":
        _color(r.headline, RGBColor(0xFF, 0xFF, 0xFF))
        _shade(r.headline, str(color))
    else:
        _color(r.headline, color)
    for head, _ in r.sections.values():
        _color(head, color)
        if style == "band":
            _bar(head, color)
            _shade(head, _tint(color))
        else:
            _rule(head, color)
    for b in r.blocks:
        p = b["para"]
        if b["kind"] == "role":
            _color(p, color, only=lambda run: run._r is p.runs[0]._r)
            _color(p, MUTED, only=lambda run: bool(run.italic))
        else:
            _color(p, color)
        for bp in b["bullets"]:
            _color(bp, color, only=lambda run: bool(run.bold))
    if r.summary is not None:
        _color(r.summary, color, only=lambda run: bool(run.bold))


def _remove(p):
    p._p.getparent().remove(p._p)


def _apply_resume(r: _Resume, spec: dict):
    if "headline" in spec:
        _set_text(r.headline, spec["headline"])
    if "summary" in spec and r.summary is not None:
        _set_rich(r.summary, spec["summary"])
    if "skills" in spec:
        if not r.skills:
            raise SpecError("The base resume has no skills lines to replace.")
        tpl = r.skills[0]
        sep = tpl.runs[0].text[len(tpl.runs[0].text.rstrip().rstrip(":").rstrip()):] or ": "
        anchor = r.skill_head
        made = []
        for item in spec["skills"]:
            p = _clone_after(anchor, tpl)
            p.runs[0].text = f'{item["label"].strip()}{sep}'
            p.runs[1].text = item["text"].strip()
            for extra in p.runs[2:]:
                extra.text = ""
            anchor = p
            made.append(p)
        for p in r.skills:
            _remove(p)
        r.skills = made
    any_bullet = next((p for b in r.blocks for p in b["bullets"]), None)
    for key, bullets in (spec.get("experience") or {}).items():
        b = r.block(key)
        tpl = b["bullets"][0] if b["bullets"] else any_bullet
        if tpl is None and bullets:
            raise SpecError("The base resume has no bullet points to copy the style from.")
        anchor = b["para"]
        made = []
        for text in bullets:
            anchor = _clone_after(anchor, tpl, text.strip(), rich=True)
            made.append(anchor)
        for p in b["bullets"]:
            _remove(p)
        b["bullets"] = made
        if not bullets and b["kind"] == "group":
            _remove(b["para"])
            r.blocks.remove(b)


def _cover(base: Path, spec: dict, company: str, today: dt.date, color: RGBColor | None = None) -> docx.Document:
    c = docx.Document(base)
    r = _Resume(c)
    ps = list(c.paragraphs)
    tpl = r.summary or r.headline
    keep = []
    for p in ps:
        if p._p is r.headline._p:
            break
        keep.append(p._p)  # name and contact lines
    for p in ps:
        if p._p not in keep and p._p is not tpl._p:
            _remove(p)
    name = ps[0].text.strip() if keep else ""
    to =(spec.get("to") or "Hiring Team").strip()
    if company.lower() not in to.lower():
        to = f"{to}\n{company}"
    lines = [f"{today:%B} {today.day}, {today.year}", to, spec.get("greeting") or "Dear Hiring Team,",
             *spec.get("paragraphs", []), f'{spec.get("sign_off") or "Sincerely,"}\n{name}'.strip()]
    _set_text(tpl, lines[0])
    tpl.runs[0].bold = None  # the template may be a summary that starts with a bold figure
    tpl.runs[0].font.color.rgb = None
    tpl.paragraph_format.space_before = Pt(14)
    tpl.paragraph_format.space_after = Pt(10)
    anchor = tpl
    for text in lines[1:]:
        anchor = _clone_after(anchor, tpl, text)
        anchor.paragraph_format.space_before = Pt(0)
    if color is not None:
        head = [p for p in c.paragraphs if p._p in keep]
        _header(head, color)
        filled = [p for p in head if p.text.strip()]
        if filled:
            _rule(filled[-1], color)  # letterhead line
    return c


# ---------- PDF ----------

def to_pdf(path: Path) -> Path | None:
    """LibreOffice conversion next to the .docx. None when LibreOffice isn't installed or fails."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    # A user id with no home (Docker) can't make LibreOffice's profile: give it its own.
    lo_profile = Path(tempfile.gettempdir()) / f"lo-profile-{os.getuid()}"
    try:
        subprocess.run([soffice, f"-env:UserInstallation={lo_profile.as_uri()}", "--headless", "--convert-to", "pdf",
                        "--outdir", str(path.parent), str(path)], capture_output=True, timeout=180, check=True)
    except (subprocess.SubprocessError, OSError):
        return None
    out = path.with_suffix(".pdf")
    return out if out.exists() else None


def pdf_pages(path: Path) -> int:
    from .preflight import pdf_text
    got = pdf_text(path)
    return got[1] if got else 0


# ---------- fitting to the page limit ----------

# Small, fixed steps that a reader doesn't notice, tried in order when the resume runs over: margins, then the space
# between paragraphs, then the type a half point at a time. Never below MIN_BODY_PT. Past the last step, the agent
# shortens the text instead (build reports it as a problem).
FIT_STEPS = [(0.6, 1.0, 0.0), (0.5, 0.7, 0.0), (0.5, 0.5, 0.5), (0.5, 0.4, 1.0)]  # (margin in, spacing x, smaller pt)
MIN_BODY_PT = 9.5


def _body_pt(d) -> float:
    size = d.styles["Normal"].font.size
    if size is not None:
        return size.pt
    sz = d.styles.element.find(qn("w:docDefaults") + "/" + qn("w:rPrDefault") + "/" + qn("w:rPr") + "/" + qn("w:sz"))
    return int(sz.get(qn("w:val"))) / 2 if sz is not None else 11.0


def _tighten(d, margin_in: float, spacing: float, smaller: float):
    from docx.shared import Inches
    for s in d.sections:
        for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
            if getattr(s, side) is None or getattr(s, side) > Inches(margin_in):
                setattr(s, side, Inches(margin_in))
    for p in d.paragraphs:
        f = p.paragraph_format
        for k in ("space_before", "space_after"):
            v = getattr(f, k)
            if v is None and p.style is not None:
                v = getattr(p.style.paragraph_format, k)
            if v:
                setattr(f, k, Pt(round(v.pt * spacing, 1)))
    if smaller:
        body = _body_pt(d)
        new_body = max(MIN_BODY_PT, body - smaller)
        d.styles["Normal"].font.size = Pt(new_body)
        cut = body - new_body
        for p in d.paragraphs:
            for r in p.runs:
                if r.font.size is not None:
                    r.font.size = Pt(max(MIN_BODY_PT, r.font.size.pt - cut))


def _fit(path: Path, pages: int, out: dict) -> int:
    """Re-lays the resume with FIT_STEPS until it fits in MAX_PAGES. Returns the final page count."""
    original = path.read_bytes()
    import io
    for i, step in enumerate(FIT_STEPS, 1):
        d = docx.Document(io.BytesIO(original))
        _tighten(d, *step)
        d.save(path)
        made = to_pdf(path)
        if made is None:
            break
        pages = pdf_pages(made)
        if pages <= MAX_PAGES["resume"]:
            out["fit"] = f"Tightened the layout (step {i} of {len(FIT_STEPS)}) to fit {MAX_PAGES['resume']} pages."
            return pages
    path.write_bytes(original)  # nothing fit: keep the untouched layout so shortening the text starts clean
    made = to_pdf(path)
    return pdf_pages(made) if made else pages


# ---------- build ----------

def _safe(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', " ", s).strip()


def build(base: Path, spec: dict, out_dir: Path, last_name: str = "", pdf: bool = True,
          today: dt.date | None = None, look=None, accent=None) -> dict:
    color, style = resolve_look(look, accent)
    company = _safe(str(spec.get("company") or ""))
    if not company:
        raise SpecError('The spec needs "company".')
    base = Path(base)
    last = _safe(last_name) or base.stem.split(" Resume")[0].strip() or "My"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    today = today or dt.date.today()

    d = docx.Document(base)
    r = _Resume(d)
    _apply_resume(r, spec.get("resume") or {})
    if color is not None:
        _theme(d, color, style)
    out = {"resume": str(out_dir / f"{last} Resume - {company}.docx")}
    d.save(out["resume"])
    if spec.get("cover_letter"):
        out["cover_letter"] = str(out_dir / f"{last} Cover Letter - {company}.docx")
        _cover(base, spec["cover_letter"], company, today, color).save(out["cover_letter"])

    problems = []
    if pdf:
        out["pdf"], out["pages"] = {}, {}
        for kind in ("resume", "cover_letter"):
            if kind not in out:
                continue
            made = to_pdf(Path(out[kind]))
            if made is None:
                problems.append(f"PDF not made for {Path(out[kind]).name}: LibreOffice (soffice) is missing or failed.")
                continue
            out["pdf"][kind] = str(made)
            n = out["pages"][kind] = pdf_pages(made)
            if n > MAX_PAGES[kind] and kind == "resume":
                n = out["pages"][kind] = _fit(Path(out[kind]), n, out)
            if n > MAX_PAGES[kind]:
                what = "Resume" if kind == "resume" else "Cover letter"
                msg = f"{what} is {n} pages; the limit is {MAX_PAGES[kind]}."
                if kind == "resume":
                    longest = sorted(r.blocks, key=lambda b: -sum(len(p.text) for p in b["bullets"]))[:3]
                    msg += " Shorten or drop bullets, longest first: " + ", ".join(
                        f'{b["heading"]} ({len(b["bullets"])} bullets)' for b in longest if b["bullets"])
                else:
                    msg += " Cut a paragraph or shorten them."
                problems.append(msg)
    out["problems"] = problems
    return out
