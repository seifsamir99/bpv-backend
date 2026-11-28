"""Documents the Newell Assistant drafts: Word (.docx) and PDF on Newell's letterhead.

The engine's create_document tool (assistant_engine.py) hands a spec to
clean_spec(); the spec is kept as a draft while the answer is self-checked (a
correction can redo it), and only the final version is rendered here, by
api.py, and saved with the answer in assistant_files.

The body is a small Markdown subset: paragraphs, #/##/### headings, - bullets,
1. numbered lists, | tables |, **bold**, *italic*, --- rules, and [placeholders]
that are highlighted for Seif to fill in.

Never adds a signature image or the company stamp: a letter leaves a blank
space to sign. English only for now (the PDF has no Arabic font on Railway).
Read directives/accounting_assistant.md before changing this.
"""

import io
import os
import re
from datetime import date, datetime, timedelta, timezone
from xml.sax.saxutils import escape

COMPANY = "Newell Electromechanical Works L.L.C"
KINDS = ("letter", "memo", "report", "policy", "other")
MAX_DOCS = 3            # per answer
MAX_BODY = 60_000       # characters
DUBAI = timezone(timedelta(hours=4))  # no DST, so no tzdata needed

NAVY = (0x0E, 0x2A, 0x47)
MARK = "#FFF0A0"        # placeholder highlight

CREATE_DOCUMENT_TOOL = {
    "name": "create_document",
    "description": (
        "Make a document Seif can download as Word and PDF on Newell's letterhead: a letter (to the FTA, a client "
        "such as Sumer, a supplier, a bank, an employee), an internal memo, a report, or a policy. Use it whenever he "
        "asks for a letter or document to send, print or keep, not for a quick answer. The letterhead already "
        "carries Newell's name, P.O. Box, phone and email. Never write a signature: the app leaves a blank space to "
        "sign, and the stamp is never added. Put anything you don't know in [square brackets], e.g. "
        "[TRN of the client], so it is highlighted for Seif to fill in. English only. To change a document, call this "
        "again with the same title and the complete new text."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(KINDS),
                     "description": "letter (date, recipient, subject, closing), memo (To/From/Subject block), "
                                    "report, policy, or other (title and body)"},
            "title": {"type": "string",
                      "description": "The subject line of a letter or memo, or the title of a report or policy"},
            "body": {"type": "string",
                     "description": "The full text in Markdown: paragraphs, ## headings, - bullets, 1. numbered "
                                    "lists, | tables |, **bold**, *italic*. For a letter, start with the salutation "
                                    "('Dear Sir,') and end with the last paragraph: the app adds the date, reference, "
                                    "recipient, subject line and the closing."},
            "recipient": {"type": "string",
                          "description": "Letter or memo: who it is addressed to, one line each for name, title, "
                                         "company, P.O. Box and emirate"},
            "reference": {"type": "string", "description": "Our reference number, if Seif gave one"},
            "date": {"type": "string", "description": "YYYY-MM-DD (default: today in Dubai)"},
            "signatory": {"type": "string",
                          "description": "Who signs, one line each for name and title, e.g. "
                                         "'Eng. Hesham Youssef\\nManaging Partner'. Letter: printed under the "
                                         "space to sign. Memo: the From line. Report or policy: 'Prepared by'."},
            "closing": {"type": "string", "description": "Letter only, default 'Yours faithfully,'"},
        },
        "required": ["kind", "title", "body"],
    },
}


class DocError(ValueError):
    """The tool input can't make a document (the message goes back to the model)."""


def today():
    return datetime.now(DUBAI).date()


def _one_line(value, limit):
    return " ".join(str(value or "").split())[:limit]


def _lines(value, limit):
    lines = [" ".join(l.split()) for l in str(value or "").replace("\\n", "\n").splitlines()]
    return "\n".join(l for l in lines if l)[:limit]


def clean_spec(inp):
    """Validate a create_document input. Returns the spec dict; raises DocError."""
    if not isinstance(inp, dict):
        raise DocError("the input must be an object with kind, title and body")
    kind = inp.get("kind") if inp.get("kind") in KINDS else "other"
    title = _one_line(inp.get("title"), 1000)
    if not title:
        raise DocError("title is required")
    if len(title) > 200:
        raise DocError("title is over 200 characters; shorten it and put the detail in the body")
    body = str(inp.get("body") or "").replace("\r\n", "\n").strip()
    if not body:
        raise DocError("body is required: the complete text of the document")
    if len(body) > MAX_BODY:
        raise DocError(f"body is over {MAX_BODY:,} characters; split it into separate documents")
    raw_date = str(inp.get("date") or "").strip()
    if raw_date:
        try:
            doc_date = date.fromisoformat(raw_date[:10])
        except ValueError:
            raise DocError("date must be YYYY-MM-DD")
    else:
        doc_date = today()
    spec = {
        "kind": kind,
        "title": title,
        "body": body,
        "recipient": _lines(inp.get("recipient"), 1000),
        "reference": _one_line(inp.get("reference"), 100),
        "date": doc_date.isoformat(),
        "signatory": _lines(inp.get("signatory"), 300),
        "closing": _one_line(inp.get("closing"), 100) or "Yours faithfully,",
    }
    if any(re.search(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]", str(v)) for v in spec.values()):
        raise DocError("documents are English only for now (the PDF can't print Arabic yet). Write the document "
                       "in English, and give any Arabic wording in your reply instead.")
    return spec


def as_text(spec):
    """The document as plain text, for the checker and for later turns of the chat."""
    head = [f'<document kind="{spec["kind"]}" title="{escape(spec["title"])}" date="{spec["date"]}">']
    if spec["reference"]:
        head.append(f"Ref: {spec['reference']}")
    if spec["recipient"]:
        head.append("To: " + spec["recipient"].replace("\n", ", "))
    if spec["signatory"]:
        head.append("Signatory: " + spec["signatory"].replace("\n", ", "))
    return "\n".join(head) + "\n\n" + spec["body"] + "\n</document>"


def file_base(spec):
    name = re.sub(r"[^A-Za-z0-9 .,&()'-]+", " ", spec["title"])
    name = " ".join(name.split()).strip(" .") or "Document"
    return f"{name[:80].rstrip(' .')} - {spec['date']}"


def long_date(iso):
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B %Y')}"


# ---------------------------------------------------------------------------
# Markdown subset -> blocks and runs
# ---------------------------------------------------------------------------

_HEAD = re.compile(r"^(#{1,4})\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_UL = re.compile(r"^(\s*)[-*•]\s+(.*)$")
_OL = re.compile(r"^(\s*)(\d{1,3}|[a-zA-Z])[.)]\s+(.*)$")
_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
_LINK = re.compile(r"\[([^\[\]\n]+)\]\((https?://[^)\s]+)\)")
_TOKEN = re.compile(r"\*\*(.+?)\*\*|\*(?![\s*])([^*\n]+?)\*|`([^`\n]+)`|(\[[^\[\]\n]{1,120}\])")
_NUMERIC = re.compile(r"^\(?-?(AED\s*)?[\d,]+(\.\d+)?%?\)?$")


def _cells(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def parse_blocks(text):
    """('h', level, text) | ('p', text) | ('list', [(level, label, text)]) | ('table', rows) | ('rule',)"""
    lines = text.replace("\r\n", "\n").split("\n")
    blocks, para, i = [], [], 0

    def flush():
        if para:
            blocks.append(("p", "\n".join(para)))
            para.clear()

    while i < len(lines):
        line, s = lines[i], lines[i].strip()
        if not s:
            flush()
            i += 1
            continue
        if _RULE.match(s):
            flush()
            blocks.append(("rule",))
            i += 1
            continue
        m = _HEAD.match(s)
        if m:
            flush()
            blocks.append(("h", len(m.group(1)), m.group(2)))
            i += 1
            continue
        if s.startswith("|") and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1].strip()):
            flush()
            rows = [_cells(s)]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            width = max(len(r) for r in rows)
            blocks.append(("table", [r + [""] * (width - len(r)) for r in rows]))
            continue
        if _UL.match(line) or _OL.match(line):
            flush()
            items = []
            while i < len(lines):
                mu, mo = _UL.match(lines[i]), _OL.match(lines[i])
                if mu:
                    items.append([1 if len(mu.group(1).expandtabs(4)) >= 2 else 0, "•", mu.group(2).strip()])
                elif mo:
                    items.append([1 if len(mo.group(1).expandtabs(4)) >= 2 else 0, mo.group(2) + ".", mo.group(3).strip()])
                elif lines[i].strip() and lines[i][:1] in (" ", "\t") and items:
                    items[-1][2] += " " + lines[i].strip()  # wrapped item
                else:
                    break
                i += 1
            blocks.append(("list", [tuple(it) for it in items]))
            continue
        para.append(s)
        i += 1
    flush()
    return blocks


def runs(text, bold=False):
    """Inline Markdown -> [(text, bold, italic, placeholder)]. Text may hold \\n line breaks."""
    text = _LINK.sub(r"\1 (\2)", text)
    out, pos = [], 0
    for m in _TOKEN.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], bold, False, False))
        if m.group(1) is not None:
            out.extend(runs(m.group(1), bold=True))
        elif m.group(2) is not None:
            out.append((m.group(2), bold, True, False))
        elif m.group(3) is not None:
            out.append((m.group(3), bold, False, False))
        else:
            out.append((m.group(4), bold, False, True))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], bold, False, False))
    return [r for r in out if r[0]]


def _plain(text):
    return "".join(r[0] for r in runs(text))


# ---------------------------------------------------------------------------
# Letterhead assets
# ---------------------------------------------------------------------------

def _assets():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "assets")
    return path if os.path.isdir(path) else os.path.join(here, "..", "assets")


def _letterhead():
    """(full page jpg path, header jpg path, footer jpg bytes cropped from the full page)."""
    from PIL import Image
    full = os.path.join(_assets(), "letterhead.jpg")
    header = os.path.join(_assets(), "letterhead_header.jpg")
    if not (os.path.exists(full) and os.path.exists(header)):
        raise FileNotFoundError("letterhead images missing from assets/")
    with Image.open(full) as img:
        w, h = img.size
        footer = img.crop((0, int(h * 0.947), w, h))  # the navy/gold band at the foot of the page
        buf = io.BytesIO()
        footer.convert("RGB").save(buf, format="JPEG", quality=92)
    return full, header, buf.getvalue()


# ---------------------------------------------------------------------------
# PDF (ReportLab)
# ---------------------------------------------------------------------------

def _pdf_markup(text, bold=False):
    parts = []
    for t, b, it, mark in runs(text, bold):
        s = escape(t).replace("\n", "<br/>")
        if mark:
            s = f'<span backColor="{MARK}">{s}</span>'
        if it:
            s = f"<i>{s}</i>"
        if b:
            s = f"<b>{s}</b>"
        parts.append(s)
    return "".join(parts)


def _pdf_styles():
    from reportlab.lib.colors import Color
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle
    navy = Color(*(c / 255 for c in NAVY))
    base = ParagraphStyle("body", fontName="Helvetica", fontSize=10.5, leading=15, spaceAfter=7)
    return {
        "body": base,
        "right": ParagraphStyle("right", parent=base, alignment=TA_RIGHT, spaceAfter=0),
        "tight": ParagraphStyle("tight", parent=base, spaceAfter=0),
        "title": ParagraphStyle("title", parent=base, fontName="Helvetica-Bold", fontSize=15, leading=19,
                                alignment=TA_CENTER, textColor=navy, spaceAfter=4),
        "meta": ParagraphStyle("meta", parent=base, fontSize=9, leading=12, alignment=TA_CENTER,
                               textColor=Color(0.35, 0.35, 0.35), spaceAfter=4),
        "subject": ParagraphStyle("subject", parent=base, fontName="Helvetica-Bold", spaceBefore=4, spaceAfter=10),
        "h1": ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=13, leading=17,
                             textColor=navy, spaceBefore=8, spaceAfter=5),
        "h2": ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=11.5, leading=15,
                             textColor=navy, spaceBefore=6, spaceAfter=4),
        "h3": ParagraphStyle("h3", parent=base, fontName="Helvetica-Bold", spaceBefore=4, spaceAfter=3),
        "cell": ParagraphStyle("cell", parent=base, fontSize=9.5, leading=12.5, spaceAfter=0),
        "cell_r": ParagraphStyle("cell_r", parent=base, fontSize=9.5, leading=12.5, spaceAfter=0, alignment=TA_RIGHT),
        "navy": navy,
    }


def _col_widths(rows, total):
    lengths = [max(min(len(_plain(r[c])), 40) for r in rows) + 4 for c in range(len(rows[0]))]
    scale = total / sum(lengths)
    return [l * scale for l in lengths]


def _pdf_body(blocks, st, width):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Paragraph, Spacer, Table, TableStyle

    out = []
    for b in blocks:
        if b[0] == "p":
            out.append(Paragraph(_pdf_markup(b[1]), st["body"]))
        elif b[0] == "h":
            out.append(Paragraph(_pdf_markup(b[2]), st["h%d" % min(b[1], 3)]))
        elif b[0] == "rule":
            out.append(HRFlowable(width="100%", thickness=0.6, color=colors.grey, spaceBefore=2, spaceAfter=8))
        elif b[0] == "list":
            for level, label, text in b[1]:
                indent = (6 + 6 * level) * mm
                style = st["body"].clone("li", leftIndent=indent, bulletIndent=indent - 5 * mm, spaceAfter=3)
                out.append(Paragraph(_pdf_markup(text), style, bulletText=label))
            out[-1].style = out[-1].style.clone("li_last", spaceAfter=8)
        elif b[0] == "table":
            rows = b[1]
            data = []
            for r, row in enumerate(rows):
                cells = []
                for cell in row:
                    style = st["cell_r"] if r and _NUMERIC.match(_plain(cell).strip() or "x") else st["cell"]
                    cells.append(Paragraph(_pdf_markup(cell, bold=(r == 0)), style))
                data.append(cells)
            table = Table(data, colWidths=_col_widths(rows, width), repeatRows=1, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, colors.Color(0.6, 0.6, 0.6)),
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.9, 0.92, 0.95)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            out.append(table)
            out.append(Spacer(1, 4 * mm))
    return out


def _pdf_story(spec, width):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, Spacer, Table, TableStyle

    st = _pdf_styles()
    blocks = parse_blocks(spec["body"])
    when = long_date(spec["date"])
    story = []
    plain = TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                        ("VALIGN", (0, 0), (-1, -1), "TOP")])

    if spec["kind"] == "letter":
        ref = f"<b>Ref:</b> {escape(spec['reference'])}" if spec["reference"] else ""
        top = Table([[Paragraph(ref, st["tight"]), Paragraph(f"<b>Date:</b> {when}", st["right"])]],
                    colWidths=[width * 0.6, width * 0.4])
        top.setStyle(plain)
        story += [top, Spacer(1, 6 * mm)]
        if spec["recipient"]:
            lines = spec["recipient"].split("\n")
            story.append(Paragraph("<br/>".join([_pdf_markup(lines[0], bold=True)] + [_pdf_markup(l) for l in lines[1:]]),
                                   st["body"]))
            story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(f"<u>Subject: {_pdf_markup(spec['title'])}</u>", st["subject"]))
        story += _pdf_body(blocks, st, width)
        closing = [Spacer(1, 3 * mm), Paragraph(escape(spec["closing"]), st["tight"]), Spacer(1, 20 * mm)]
        sign = spec["signatory"].split("\n") if spec["signatory"] else []
        if sign:
            closing.append(Paragraph(f"<b>{escape(sign[0])}</b>", st["tight"]))
            closing += [Paragraph(escape(l), st["tight"]) for l in sign[1:]]
        else:
            closing.append(Paragraph("_______________________", st["tight"]))
        closing.append(Paragraph(f"For {COMPANY}", st["tight"]))
        story.append(KeepTogether(closing))

    elif spec["kind"] == "memo":
        story.append(Paragraph("MEMORANDUM", st["title"]))
        story.append(Spacer(1, 3 * mm))
        rows = [("To", spec["recipient"].replace("\n", ", ") or "[Recipient]"),
                ("From", spec["signatory"].replace("\n", ", ") or COMPANY),
                ("Date", when)]
        if spec["reference"]:
            rows.append(("Ref", spec["reference"]))
        rows.append(("Subject", spec["title"]))
        head = Table([[Paragraph(f"<b>{k}:</b>", st["tight"]), Paragraph(_pdf_markup(v), st["tight"])]
                      for k, v in rows], colWidths=[22 * mm, width - 22 * mm])
        head.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                                  ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story += [head, HRFlowable(width="100%", thickness=1, color=st["navy"], spaceBefore=4, spaceAfter=10)]
        story += _pdf_body(blocks, st, width)

    else:
        story.append(Paragraph(_pdf_markup(spec["title"]), st["title"]))
        meta = [when] + ([f"Ref: {escape(spec['reference'])}"] if spec["reference"] else [])
        story.append(Paragraph(" · ".join(meta), st["meta"]))
        story.append(HRFlowable(width="100%", thickness=1, color=st["navy"], spaceBefore=2, spaceAfter=10))
        story += _pdf_body(blocks, st, width)
        if spec["signatory"]:
            story.append(Spacer(1, 4 * mm))
            story.append(Paragraph("<b>Prepared by:</b> " + escape(spec["signatory"].replace("\n", ", ")), st["tight"]))
    return story


def render_pdf(spec):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate

    full, header, _ = _letterhead()
    from PIL import Image
    with Image.open(header) as img:
        iw, ih = img.size
    top = A4[0] * ih / iw + 14
    bottom = A4[1] * 0.10 + 6

    def build(total):
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=top, bottomMargin=bottom,
                                leftMargin=20 * mm, rightMargin=20 * mm,
                                title=spec["title"], author=COMPANY, creator="Newell Assistant")
        pages = [0]

        def on_page(canvas, d):
            canvas.saveState()
            canvas.drawImage(full, 0, 0, width=A4[0], height=A4[1], preserveAspectRatio=False, mask="auto")
            if total and total > 1:
                canvas.setFont("Helvetica", 8)
                canvas.setFillGray(0.4)
                canvas.drawRightString(A4[0] - 20 * mm, A4[1] * 0.10 - 30, f"Page {d.page} of {total}")
            canvas.restoreState()
            pages[0] = d.page

        doc.build(_pdf_story(spec, doc.width - 12), onFirstPage=on_page, onLaterPages=on_page)
        return buf.getvalue(), pages[0]

    data, n = build(None)
    if n > 1:
        data, _ = build(n)
    return data


# ---------------------------------------------------------------------------
# Word (python-docx)
# ---------------------------------------------------------------------------

def _docx_runs(paragraph, text, bold=False, size=None, color=None):
    from docx.enum.text import WD_COLOR_INDEX
    from docx.shared import Pt, RGBColor
    for t, b, it, mark in runs(text, bold):
        pieces = t.split("\n")
        for n, piece in enumerate(pieces):
            if n:
                paragraph.add_run().add_break()
            if not piece:
                continue
            run = paragraph.add_run(piece)
            run.bold = b or None
            run.italic = it or None
            if mark:
                run.font.highlight_color = WD_COLOR_INDEX.YELLOW
            if size:
                run.font.size = Pt(size)
            if color:
                run.font.color.rgb = RGBColor(*color)


def _docx_para(doc, text="", bold=False, size=None, color=None, align=None, after=6, before=0):
    from docx.shared import Pt
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.space_before = Pt(before)
    if align is not None:
        p.alignment = align
    if text:
        _docx_runs(p, text, bold=bold, size=size, color=color)
    return p


def _shade(cell, hex_fill):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    cell._tc.get_or_add_tcPr().append(shd)


def _no_borders(table):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:val"), "nil")
        borders.append(el)
    table._tbl.tblPr.append(borders)


def _docx_body(doc, blocks, width_mm):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm, Pt
    for b in blocks:
        if b[0] == "p":
            _docx_para(doc, b[1])
        elif b[0] == "h":
            size = {1: 13, 2: 11.5}.get(b[1], 10.5)
            _docx_para(doc, b[2], bold=True, size=size, color=NAVY if b[1] <= 2 else None, before=6, after=4)
        elif b[0] == "rule":
            _docx_para(doc, "_" * 60, color=(0x99, 0x99, 0x99), align=WD_ALIGN_PARAGRAPH.CENTER)
        elif b[0] == "list":
            for n, (level, label, text) in enumerate(b[1]):
                p = _docx_para(doc, after=3 if n < len(b[1]) - 1 else 8)
                indent = Mm(6 + 6 * level)
                p.paragraph_format.left_indent = indent
                p.paragraph_format.first_line_indent = -Mm(5)
                p.paragraph_format.tab_stops.add_tab_stop(indent)
                p.add_run(label + "\t")
                _docx_runs(p, text)
        elif b[0] == "table":
            rows = b[1]
            table = doc.add_table(rows=len(rows), cols=len(rows[0]))
            table.style = "Table Grid"
            widths = _col_widths(rows, width_mm)
            for r, row in enumerate(rows):
                for c, text in enumerate(row):
                    cell = table.cell(r, c)
                    cell.width = Mm(widths[c])
                    p = cell.paragraphs[0]
                    p.paragraph_format.space_after = Pt(0)
                    if r and _NUMERIC.match(_plain(text).strip() or "x"):
                        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                    _docx_runs(p, text, bold=(r == 0), size=9.5)
                    if r == 0:
                        _shade(cell, "E6EBF2")
            _docx_para(doc, after=4)


def render_docx(spec):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm, Pt

    _, header_jpg, footer_jpg = _letterhead()
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    sec.left_margin = sec.right_margin = Mm(20)
    sec.top_margin, sec.bottom_margin = Mm(50), Mm(24)
    sec.header_distance = sec.footer_distance = Mm(0)
    for part, image in ((sec.header, header_jpg), (sec.footer, io.BytesIO(footer_jpg))):
        p = part.paragraphs[0]
        p.paragraph_format.left_indent = p.paragraph_format.right_indent = -Mm(20)
        p.paragraph_format.space_after = Pt(0)
        p.add_run().add_picture(image, width=Mm(210))

    normal = doc.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10.5)
    normal.paragraph_format.line_spacing = 1.15
    doc.core_properties.title = spec["title"]
    doc.core_properties.author = COMPANY

    width = 170  # mm between margins
    blocks = parse_blocks(spec["body"])
    when = long_date(spec["date"])

    if spec["kind"] == "letter":
        top = doc.add_table(rows=1, cols=2)
        _no_borders(top)
        left, right = top.cell(0, 0).paragraphs[0], top.cell(0, 1).paragraphs[0]
        if spec["reference"]:
            left.add_run("Ref: ").bold = True
            left.add_run(spec["reference"])
        right.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        right.add_run("Date: ").bold = True
        right.add_run(when)
        _docx_para(doc, after=6)
        if spec["recipient"]:
            lines = spec["recipient"].split("\n")
            p = _docx_para(doc, after=8)
            _docx_runs(p, lines[0], bold=True)
            for line in lines[1:]:
                p.add_run().add_break()
                _docx_runs(p, line)
        p = _docx_para(doc, after=10, before=4)
        r = p.add_run("Subject: ")
        r.bold = r.underline = True
        start = len(p.runs)
        _docx_runs(p, spec["title"], bold=True)
        for run in p.runs[start:]:
            run.underline = True
        _docx_body(doc, blocks, width)
        p = _docx_para(doc, spec["closing"], before=4, after=0)
        p.paragraph_format.keep_with_next = True
        for _ in range(4):
            gap = _docx_para(doc, after=0)
            gap.paragraph_format.keep_with_next = True
        sign = spec["signatory"].split("\n") if spec["signatory"] else ["_______________________"]
        for n, line in enumerate(sign):
            p = _docx_para(doc, after=0)
            p.add_run(line).bold = (n == 0 and bool(spec["signatory"])) or None
            p.paragraph_format.keep_with_next = True
        _docx_para(doc, f"For {COMPANY}", after=0)

    elif spec["kind"] == "memo":
        _docx_para(doc, "MEMORANDUM", bold=True, size=15, color=NAVY, align=WD_ALIGN_PARAGRAPH.CENTER, after=8)
        rows = [("To", spec["recipient"].replace("\n", ", ") or "[Recipient]"),
                ("From", spec["signatory"].replace("\n", ", ") or COMPANY),
                ("Date", when)]
        if spec["reference"]:
            rows.append(("Ref", spec["reference"]))
        rows.append(("Subject", spec["title"]))
        table = doc.add_table(rows=len(rows), cols=2)
        _no_borders(table)
        for r, (k, v) in enumerate(rows):
            table.cell(r, 0).width, table.cell(r, 1).width = Mm(22), Mm(width - 22)
            table.cell(r, 0).paragraphs[0].add_run(k + ":").bold = True
            _docx_runs(table.cell(r, 1).paragraphs[0], v)
        _docx_para(doc, "_" * 75, color=NAVY, after=8)
        _docx_body(doc, blocks, width)

    else:
        _docx_para(doc, spec["title"], bold=True, size=15, color=NAVY, align=WD_ALIGN_PARAGRAPH.CENTER, after=2)
        meta = [when] + ([f"Ref: {spec['reference']}"] if spec["reference"] else [])
        _docx_para(doc, " · ".join(meta), size=9, color=(0x59, 0x59, 0x59), align=WD_ALIGN_PARAGRAPH.CENTER, after=2)
        _docx_para(doc, "_" * 75, color=NAVY, after=8)
        _docx_body(doc, blocks, width)
        if spec["signatory"]:
            p = _docx_para(doc, before=6)
            p.add_run("Prepared by: ").bold = True
            p.add_run(spec["signatory"].replace("\n", ", "))

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def render(spec):
    """[(filename, mime, bytes)]: the Word file, then the PDF."""
    base = file_base(spec)
    return [(base + ".docx", DOCX_MIME, render_docx(spec)),
            (base + ".pdf", "application/pdf", render_pdf(spec))]
