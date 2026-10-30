"""
AI-assisted data extraction from documents using Claude Opus 4.5
Extracts structured data from quotations, invoices, and other documents
"""

import os
import time
import base64
import anthropic
from dotenv import load_dotenv
import json
from datetime import datetime

load_dotenv()


class AIExtractor:
    """Extract structured data from documents using Claude"""

    def __init__(self):
        api_key = os.getenv('ANTHROPIC_API_KEY')
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set in environment")

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = "claude-opus-4-5-20251101"  # Latest Opus 4.5
        # Higher-capacity model used automatically when the primary stays
        # overloaded (529). Sonnet rarely 529s and handles this extraction well.
        self.fallback_model = "claude-sonnet-5"

    # HTTP statuses worth retrying: rate limit, overloaded, transient 5xx
    _RETRYABLE = {408, 409, 429, 500, 502, 503, 504, 529}

    def _create_with_retry(self, *, max_tokens, messages, max_attempts=4):
        """Call Claude with exponential backoff on transient overload/rate/5xx
        errors, then fall back to a higher-capacity model if the primary model
        stays overloaded. Non-transient errors (auth, bad request, insufficient
        credits) are raised immediately so they surface clearly."""
        models = [self.model]
        if self.fallback_model and self.fallback_model != self.model:
            models.append(self.fallback_model)

        last_err = None
        for model in models:
            for attempt in range(max_attempts):
                try:
                    return self.client.messages.create(
                        model=model, max_tokens=max_tokens, messages=messages)
                except anthropic.APIStatusError as e:
                    last_err = e
                    if getattr(e, 'status_code', None) not in self._RETRYABLE:
                        raise  # real error — don't mask it behind retries
                except anthropic.APIConnectionError as e:
                    last_err = e
                if attempt < max_attempts - 1:
                    time.sleep(min(2 ** attempt, 8))  # 1s, 2s, 4s
        raise last_err

    _MEDIA_TYPES = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp'
    }

    def _encode_image(self, image_path):
        """Return (base64 data, media type) for an image file"""
        with open(image_path, 'rb') as f:
            image_data = base64.standard_b64encode(f.read()).decode('utf-8')
        ext = os.path.splitext(image_path)[1].lower()
        return image_data, self._MEDIA_TYPES.get(ext, 'image/jpeg')

    def extract_from_image(self, image_path, document_type):
        """Extract data from image file"""
        return self._extract([self._encode_image(image_path)], document_type)

    def extract_from_images(self, image_paths, document_type):
        """Extract data from several images that are pages of ONE document
        (e.g. a multi-page PDF rendered page by page) in a single request."""
        return self._extract([self._encode_image(p) for p in image_paths], document_type)

    def extract_from_pdf(self, pdf_path, document_type):
        """Extract data from PDF file"""
        # For PDFs, we need to convert to images first or use PDF reading
        # For now, let's assume PDF is converted to image externally
        # In production, use pdf2image or similar
        raise NotImplementedError("PDF extraction coming soon. Convert PDF to image first.")

    def _extract(self, images, document_type):
        """Core extraction logic. `images` is a list of (base64 data, media type)
        pairs — the pages of one document, in order."""

        # Define extraction prompts for each document type
        prompts = {
            "quotation": """
Extract the following information from this quotation document:

Return ONLY a JSON object with this exact structure:
{
  "vendor_name": "string",
  "vendor_contact": "string",
  "vendor_email": "string",
  "vendor_phone": "string",
  "vendor_address": "string",
  "quotation_number": "string",
  "quotation_date": "YYYY-MM-DD",
  "validity_date": "YYYY-MM-DD",
  "payment_terms": "string",
  "currency": "AED",
  "line_items": [
    {
      "description": "string",
      "quantity": number,
      "unit": "string",
      "unit_price": number,
      "total_price": number
    }
  ],
  "subtotal": number,
  "vat_percent": number,
  "vat_amount": number,
  "total_amount": number,
  "notes": "string"
}

If any field is not found, use empty string for strings, 0 for numbers, and empty array for arrays.
Be precise with numbers. For line items, extract all visible items.
""",
            "invoice": """
Extract the following information from this tax invoice document:

Return ONLY a JSON object with this exact structure:
{
  "vendor_name": "string",
  "vendor_trn": "string",
  "vendor_address": "string",
  "invoice_number": "string",
  "invoice_date": "YYYY-MM-DD",
  "due_date": "YYYY-MM-DD",
  "lpo_reference": "string",
  "line_items": [
    {
      "description": "string",
      "quantity": number,
      "unit": "string",
      "unit_price": number,
      "total_price": number
    }
  ],
  "subtotal": number,
  "vat_percent": number,
  "vat_amount": number,
  "total_amount": number,
  "currency": "AED",
  "notes": "string"
}

If any field is not found, use empty string for strings, 0 for numbers, and empty array for arrays.
""",
            "delivery_note": """
Extract the following information from this delivery note:

Return ONLY a JSON object with this exact structure:
{
  "delivery_note_number": "string",
  "delivery_date": "YYYY-MM-DD",
  "lpo_reference": "string",
  "vendor_name": "string",
  "received_by": "string",
  "line_items": [
    {
      "description": "string",
      "quantity_delivered": number,
      "unit": "string",
      "condition": "Good/Damaged/Rejected"
    }
  ],
  "notes": "string"
}
""",
            "job_order_quotation": """
Extract all information from this subcontractor quotation document image — this
quote will be used to issue a Job Order to the subcontractor.

CRITICAL RULES FOR LINE ITEMS:
- Extract ALL rows from the table — do not skip any.
- Combine all specification columns into ONE descriptive string per row.

COLUMN IDENTIFICATION — read the table header carefully from right to left:
  - LAST column = "Amount"/"Total" -> use as total_price
  - If present, SECOND-TO-LAST column = "Unit Price"/"Rate" -> use as unit_price
  - If present, THIRD-TO-LAST column = "Quantity"/"Qty" -> use as quantity
  - Some quotes are lump-sum (only Description + Amount, e.g. an "L/S" column) with
    no quantity or unit price at all -- that is normal, not an error.
  - Some quotes are rate-only: the Qty column holds a unit such as "Per Sqft",
    "Per Rmt" or "Per No" instead of a number, and there is no Amount column.
    Copy that text exactly as the quantity (a string) and set total_price to 0.

Set "has_unit_pricing" to true only if the table has real, separate Quantity AND
Unit Price columns (a rate-only Qty column like "Per Sqft" counts); set it to false
for lump-sum quotes (leave quantity/unit_price as 0 on each line item in that case).

Return ONLY a valid JSON object with this exact structure:
{
  "subcontractor_name": "string",
  "subcontractor_contact": "string",
  "subcontractor_phone": "string",
  "subcontractor_address": "string",
  "quotation_number": "string",
  "quotation_date": "YYYY-MM-DD",
  "payment_terms": "string",
  "line_items": [
    {
      "description": "full descriptive string",
      "quantity": number (or a string such as "Per Sqft" on a rate-only quote),
      "unit_price": number,
      "total_price": number
    }
  ],
  "has_unit_pricing": true or false,
  "discount": number,
  "vat_percent": number,
  "notes": "string"
}

If any field is not found, use empty string for strings, 0 for numbers (5 for
vat_percent), and empty array for arrays.
Return ONLY the JSON object, no additional text.
"""
        }

        prompt = prompts.get(document_type, prompts["quotation"])
        if len(images) > 1:
            prompt = (f"The {len(images)} images above are consecutive pages of ONE document. "
                      "The line-item table continues across pages — return the line items "
                      "from EVERY page, in page order, as a single list.\n" + prompt)

        content = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": image_data},
            }
            for image_data, media_type in images
        ]
        content.append({"type": "text", "text": prompt})

        # Call Claude with vision (auto-retries transient overload / falls back).
        # More pages -> more line items, so scale the output budget with them.
        message = self._create_with_retry(
            max_tokens=min(4000 * len(images), 16000),
            messages=[{"role": "user", "content": content}],
        )

        # Extract JSON from response
        response_text = message.content[0].text

        # Try to find JSON in response
        try:
            # Look for JSON object in response
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}') + 1

            if start_idx != -1 and end_idx > start_idx:
                json_str = response_text[start_idx:end_idx]
                extracted_data = json.loads(json_str)
                return {
                    "success": True,
                    "data": extracted_data,
                    "raw_response": response_text
                }
            else:
                return {
                    "success": False,
                    "error": "No JSON found in response",
                    "raw_response": response_text
                }

        except json.JSONDecodeError as e:
            return {
                "success": False,
                "error": f"Failed to parse JSON: {str(e)}",
                "raw_response": response_text
            }

    def extract_from_text(self, text, document_type):
        """Extract header fields from raw document text (no vision).

        Used for digital PDFs where line items are parsed deterministically and
        only the header needs AI. Much lighter and less overload-prone than the
        vision path, so most PDFs never touch the image model at all."""
        if document_type == 'job_order_quotation':
            prompt = (
                "Extract the header fields from this subcontractor quotation text "
                "(it will be used to issue a Job Order) and return ONLY a JSON "
                "object with this exact structure:\n"
                "{\n"
                '  "subcontractor_name": "string",\n'
                '  "subcontractor_contact": "string",\n'
                '  "subcontractor_phone": "string",\n'
                '  "subcontractor_address": "string",\n'
                '  "quotation_number": "string",\n'
                '  "quotation_date": "YYYY-MM-DD",\n'
                '  "payment_terms": "string",\n'
                '  "discount": number,\n'
                '  "vat_percent": number\n'
                "}\n"
                "If a field is not found use an empty string, 0 for discount, or 5 for vat_percent.\n"
                "discount = any stated discount amount in AED that reduces the price; 0 if none.\n\n"
                "Document text:\n" + (text or "")
            )
        else:
            prompt = (
                "Extract the header fields from this supplier quotation text and "
                "return ONLY a JSON object with this exact structure:\n"
                "{\n"
                '  "vendor_name": "string",\n'
                '  "vendor_contact": "string",\n'
                '  "vendor_email": "string",\n'
                '  "vendor_phone": "string",\n'
                '  "vendor_address": "string",\n'
                '  "vendor_trn": "string",\n'
                '  "quotation_number": "string",\n'
                '  "quotation_date": "YYYY-MM-DD",\n'
                '  "validity_date": "YYYY-MM-DD",\n'
                '  "payment_terms": "string",\n'
                '  "discount": number,\n'
                '  "vat_percent": number\n'
                "}\n"
                "If a field is not found use an empty string, 0 for discount, or 5 for vat_percent.\n"
                "discount = any stated discount amount in AED that reduces the price "
                "(e.g. a 'Discount Price' or 'Less discount' line); 0 if none.\n\n"
                "Document text:\n" + (text or "")
            )
        message = self._create_with_retry(
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        response_text = message.content[0].text
        try:
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}') + 1
            if start_idx != -1 and end_idx > start_idx:
                return {
                    "success": True,
                    "data": json.loads(response_text[start_idx:end_idx]),
                    "raw_response": response_text,
                }
            return {"success": False, "error": "No JSON found in response", "raw_response": response_text}
        except json.JSONDecodeError as e:
            return {"success": False, "error": f"Failed to parse JSON: {str(e)}", "raw_response": response_text}

    def extract_from_voice(self, audio_text, context="general"):
        """Extract structured data from voice-transcribed text

        Args:
            audio_text: Transcribed text from speech
            context: Type of data being entered (quotation/invoice/cheque/etc)
        """

        prompts = {
            "quotation": """
The user spoke this text to create a quotation. Extract the information and return JSON:

{
  "vendor_name": "string",
  "quotation_date": "YYYY-MM-DD",
  "validity_date": "YYYY-MM-DD",
  "payment_terms": "string",
  "line_items": [
    {
      "description": "string",
      "quantity": number,
      "unit": "string",
      "unit_price": number
    }
  ],
  "notes": "string"
}

Spoken text: """ + audio_text,

            "cheque": """
The user spoke this text to create a cheque. Extract the information and return JSON:

{
  "cheque_number": "string",
  "bank_name": "string",
  "cheque_date": "YYYY-MM-DD",
  "amount": number,
  "vendor_name": "string",
  "notes": "string"
}

Spoken text: """ + audio_text,

            "general": """
The user spoke this text. Extract any structured information and return as JSON.
Identify the type of document (quotation/invoice/cheque/lpo) and extract relevant fields.

Spoken text: """ + audio_text
        }

        prompt = prompts.get(context, prompts["general"])

        message = self._create_with_retry(
            max_tokens=2000,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
        )

        response_text = message.content[0].text

        try:
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}') + 1

            if start_idx != -1 and end_idx > start_idx:
                json_str = response_text[start_idx:end_idx]
                extracted_data = json.loads(json_str)
                return {
                    "success": True,
                    "data": extracted_data,
                    "raw_response": response_text
                }
        except:
            pass

        return {
            "success": False,
            "error": "Could not extract structured data",
            "raw_response": response_text
        }


# CLI for testing
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python ai_extraction.py <image_path> <quotation|invoice|delivery_note>")
        sys.exit(1)

    image_path = sys.argv[1]
    doc_type = sys.argv[2]

    extractor = AIExtractor()
    result = extractor.extract_from_image(image_path, doc_type)

    print(json.dumps(result, indent=2))
