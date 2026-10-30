"""
Read a supplier's Statement of Account (PDF or photos) with Claude and return
its invoice rows as structured data -- the "Import Statement" button on the
/supplier-ledger page.

Extraction only: nothing is written here. The page shows the result for review
and then posts it to supplier_ledger_service.import_statement().

The whole PDF (every page) or every photo goes to Claude in ONE request, so
multi-page statements come back as one list. Output is constrained with
structured outputs (a JSON schema). anthropic==0.75 predates the
`output_config` parameter, so it is passed through `extra_body`.

The statement's own printed totals are extracted too, so the page can show
whether the rows add up to what the supplier says is outstanding -- a
misread row shows up as a mismatch instead of silently entering the ledger.
"""

import base64
import json
import os

import anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-opus-5"
MAX_FILE_BYTES = 30 * 1024 * 1024  # API request limit is 32 MB

IMAGE_TYPES = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
    '.gif': 'image/gif', '.webp': 'image/webp',
}


class ExtractionError(Exception):
    """A refusal the user should see verbatim (unreadable file, wrong type)."""


_SCHEMA = {
    "type": "object",
    "properties": {
        "supplierName": {"type": "string"},
        "supplierPhone": {"type": "string"},
        "supplierAddress": {"type": "string"},
        "supplierTrn": {"type": "string"},
        "ourAccountCode": {"type": "string"},
        "statementDate": {"type": "string"},
        "currency": {"type": "string"},
        "invoices": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "invoiceDate": {"type": "string"},
                    "dueDate": {"type": "string"},
                    "reference": {"type": "string"},
                    "description": {"type": "string"},
                    "amount": {"type": "number"},
                    "cleared": {"type": "number"},
                    "balance": {"type": "number"},
                },
                "required": ["invoiceDate", "dueDate", "reference", "description",
                             "amount", "cleared", "balance"],
                "additionalProperties": False,
            },
        },
        "unallocatedPayments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "reference": {"type": "string"},
                    "amount": {"type": "number"},
                },
                "required": ["date", "reference", "amount"],
                "additionalProperties": False,
            },
        },
        "statedTotalOutstanding": {"type": "number"},
        "hasStatedTotal": {"type": "boolean"},
        "notes": {"type": "string"},
    },
    "required": ["supplierName", "supplierPhone", "supplierAddress", "supplierTrn",
                 "ourAccountCode", "statementDate", "currency", "invoices",
                 "unallocatedPayments", "statedTotalOutstanding", "hasStatedTotal", "notes"],
    "additionalProperties": False,
}

_PROMPT = """This is a supplier's Statement of Account sent to our company, Newell Electromechanical Works L.L.C. \
The supplier (the company that issued the statement, usually in the letterhead) is who we owe money to. \
It may span several pages or several photos; treat them as one statement.

Extract every invoice row on the statement, from all pages, in the order printed. Transcribe exactly -- \
these figures go into our accounts payable ledger.

Per invoice row:
- invoiceDate / dueDate: YYYY-MM-DD. UAE statements write dates day-first (29/04/2025 = 2025-04-29). \
dueDate is "" if the statement has no due date column.
- reference: the supplier's invoice / bill number exactly as printed (keep prefixes like "SD-").
- description: the row's description text; if there is none but a PO number is shown, use "PO <number>"; \
otherwise "".
- amount: the invoice (bill / debit) value.
- cleared: whatever the statement shows as already credited, cleared, received or paid against that row; 0 if none.
- balance: the row's pending / balance / outstanding value.
Use plain numbers without thousands separators.

Only invoice (debit) rows go in invoices. Month subtotal rows and grand totals are NOT invoices. \
If the statement lists payments, receipts or credit notes as their own rows that are not tied to a specific \
invoice, put them in unallocatedPayments (as positive amounts) instead.

statedTotalOutstanding: the statement's printed total outstanding / balance due (hasStatedTotal=false and 0 \
if none is printed). statementDate: the "as on" / print date, YYYY-MM-DD, or "". \
ourAccountCode: our customer code on their system if printed (e.g. CUST0149), else "". \
Empty string for any other field not shown. notes: anything a bookkeeper should know that doesn't fit the \
fields (unreadable rows, torn pages, ageing that doesn't add up); "" if nothing."""


def _content_blocks(files):
    """files: list of (filename, bytes). One PDF, or one or more images."""
    blocks = []
    total = 0
    for name, data in files:
        total += len(data)
        ext = os.path.splitext(name or '')[1].lower()
        b64 = base64.standard_b64encode(data).decode('ascii')
        if ext == '.pdf':
            blocks.append({"type": "document",
                           "source": {"type": "base64", "media_type": "application/pdf", "data": b64}})
        elif ext in IMAGE_TYPES:
            blocks.append({"type": "image",
                           "source": {"type": "base64", "media_type": IMAGE_TYPES[ext], "data": b64}})
        else:
            raise ExtractionError(f"{name}: upload a PDF or a photo (JPG/PNG)")
    if not blocks:
        raise ExtractionError("No file uploaded")
    if total > MAX_FILE_BYTES:
        raise ExtractionError("Files are too large (30 MB max in total)")
    blocks.append({"type": "text", "text": _PROMPT})
    return blocks


def extract_statement(files):
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=api_key, max_retries=4)

    # Streaming: a long statement plus thinking can run to many thousand
    # output tokens, which would risk an HTTP timeout on a plain request.
    with client.messages.stream(
        model=MODEL,
        max_tokens=64000,
        messages=[{"role": "user", "content": _content_blocks(files)}],
        extra_body={"output_config": {"format": {"type": "json_schema", "schema": _SCHEMA}}},
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        raise ExtractionError("The document could not be processed. Try a clearer scan.")
    if message.stop_reason == "max_tokens":
        raise ExtractionError("This statement is too long to read in one go. Split the PDF and import each part.")

    text = next((b.text for b in message.content if b.type == "text"), None)
    if not text:
        raise ExtractionError("No data could be read from the statement")
    return json.loads(text)
