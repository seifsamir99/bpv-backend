"""Search the Newell Assistant's library of official UAE documents.

The library (library_documents + library_chunks, built by execution/build_library.py)
holds the text of the UAE VAT, Corporate Tax, Tax Procedures and Labour laws, their
executive regulations, Cabinet / Ministerial / FTA decisions, FTA guides, public
clarifications and EmaraTax manuals, split by article or section.

Postgres full-text search, no embeddings. Read directives/assistant_library.md.
"""

import re

import db

MAX_RESULTS = 6
MAX_PER_DOCUMENT = 3
MAX_CHUNK_CHARS = 3000
CATEGORIES = ("VAT", "Corporate Tax", "Tax Procedures", "Labour")

# Laws and regulations outrank guides for the same match; EmaraTax screen
# manuals only win when nothing else matches as well.
_TYPE_WEIGHT = """CASE d.doc_type
    WHEN 'law' THEN 1.5 WHEN 'regulation' THEN 1.4
    WHEN 'cabinet_decision' THEN 1.25 WHEN 'ministerial_decision' THEN 1.25 WHEN 'fta_decision' THEN 1.25
    WHEN 'public_clarification' THEN 1.1 WHEN 'directive' THEN 1.1
    WHEN 'emaratax_manual' THEN 0.6 ELSE 1.0 END
    * CASE WHEN d.title ILIKE '%%bulletin%%' THEN 0.8 ELSE 1.0 END"""

# ts_rank with length normalisation (1). Tested on 2026-11-27 against
# ts_rank_cd(.., 32): cover density saturated near 1, so the type weight
# swamped the match and e.g. FTA Decision 17/2026 lost to generic VAT articles
# for "input tax recovery employee expenses meals".
_SQL = f"""
SELECT c.id, c.heading, c.page_from, c.page_to, c.text, d.id AS document_id, d.title, d.url,
       d.doc_type, d.category, d.issue_date,
       ts_rank(c.tsv, q, 1) * {_TYPE_WEIGHT} AS score
FROM library_chunks c
JOIN library_documents d ON d.id = c.document_id,
     {{tsquery}} AS q
WHERE d.active AND c.tsv @@ q AND (%(category)s::text IS NULL OR d.category = %(category)s)
ORDER BY score DESC, d.issue_date DESC NULLS LAST
LIMIT 40
"""

_STOP = {"the", "a", "an", "of", "for", "in", "on", "to", "and", "or", "is", "are", "what", "how",
         "does", "do", "when", "which", "uae", "under", "with", "by", "be", "can", "we", "our"}


def _or_query(text):
    words = [w for w in re.findall(r"[A-Za-z0-9]+", text.lower()) if w not in _STOP and len(w) > 1]
    return " | ".join(dict.fromkeys(words))


def search(query, category=None, limit=MAX_RESULTS):
    """Best matching passages: [{title, heading, url, pages, text, doc_type, category, issue_date}].

    All words first (websearch syntax: quotes and OR work); if that finds too
    little, any of the words, ranked by how many match and where.
    """
    query = str(query or "").strip()[:300]
    if not query:
        return []
    category = category if category in CATEGORIES else None
    rows = db.q(_SQL.format(tsquery="websearch_to_tsquery('english', %(q)s)"), {"q": query, "category": category})
    if len(rows) < limit:
        or_q = _or_query(query)
        if or_q:
            seen = {r["id"] for r in rows}
            more = db.q(_SQL.format(tsquery="to_tsquery('english', %(q)s)"), {"q": or_q, "category": category})
            rows += [r for r in more if r["id"] not in seen]

    out, per_doc = [], {}
    for r in rows:
        if per_doc.get(r["document_id"], 0) >= MAX_PER_DOCUMENT:
            continue
        per_doc[r["document_id"]] = per_doc.get(r["document_id"], 0) + 1
        text = r["text"]
        if len(text) > MAX_CHUNK_CHARS:
            text = text[:MAX_CHUNK_CHARS].rsplit(" ", 1)[0] + " …"
        pages = (f"p. {r['page_from']}" if r["page_from"] == r["page_to"] else f"pp. {r['page_from']}-{r['page_to']}") \
            if r["page_from"] else ""
        out.append({
            "title": r["title"],
            "heading": r["heading"],
            "url": _page_link(r["url"], r["page_from"]),
            "pages": pages,
            "text": text,
            "doc_type": r["doc_type"],
            "category": r["category"],
            "issue_date": r["issue_date"].isoformat() if r["issue_date"] else None,
        })
        if len(out) >= limit:
            break
    return out


def _page_link(url, page):
    """Link that opens the PDF at the passage's page (browsers honour #page=N)."""
    base = url.split("#")[0]
    return f"{base}#page={page}" if page else base


def stats():
    return db.q_one("SELECT count(*) AS documents, (SELECT count(*) FROM library_chunks) AS chunks "
                    "FROM library_documents WHERE active")
