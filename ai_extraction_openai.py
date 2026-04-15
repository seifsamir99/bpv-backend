"""
AI-assisted data extraction from documents using OpenAI GPT-4 Vision
Extracts structured data from quotations, invoices, and other documents
"""

import os
import base64
from openai import OpenAI
from dotenv import load_dotenv
import json
from datetime import datetime

load_dotenv()


class AIExtractor:
    """Extract structured data from documents using OpenAI GPT-4 Vision"""

    def __init__(self):
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set in environment")

        self.client = OpenAI(api_key=api_key)
        self.model = "gpt-4o"  # GPT-4 with vision

    def extract_from_image(self, image_path, document_type):
        """Extract data from image file"""
        # Read and encode image
        with open(image_path, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')

        # Determine media type
        ext = os.path.splitext(image_path)[1].lower()
        media_type_map = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp'
        }
        media_type = media_type_map.get(ext, 'image/jpeg')

        return self._extract(image_data, media_type, document_type)

    def extract_from_pdf(self, pdf_path, document_type):
        """Extract data from PDF file"""
        # For PDFs, we need to convert to images first or use PDF reading
        # For now, let's assume PDF is converted to image externally
        # In production, use pdf2image or similar
        raise NotImplementedError("PDF extraction coming soon. Convert PDF to image first.")

    def _extract(self, image_data, media_type, document_type):
        """Core extraction logic using OpenAI Vision"""

        # Define extraction prompts for each document type
        prompts = {
            "supplier_quotation": """
Extract all information from this supplier quotation document image.

CRITICAL RULES FOR LINE ITEMS:
- Extract ALL rows from the table — do not skip any.
- For the "description" field: combine ALL specification columns (Description, Size/OD, Wall Thickness, Length, Brand, Material grade, etc.) into ONE descriptive string. Example: "Straight Copper Pipe ASTM B280/B743 Type L ACR - 1/4\" OD 6.35mm WT 0.76mm L=5.80m - Brand: Rime-Vietnam"

COLUMN IDENTIFICATION — read the table header carefully from right to left:
  - LAST column = "Total Req Cost" or "Total (AED)" → use as total_price
  - SECOND-TO-LAST column = "Unit rate (AED)" → use as unit_price (the price per piece, e.g. 1.00, 2.00, 47.00)
  - THIRD-TO-LAST column = "Total QTY" → use as quantity (the number of pieces ordered, e.g. 50, 250, 70, 20)
  - Any column labeled "Req Qty (kg)" = weight in kilograms — DO NOT use as quantity

CRITICAL: Do NOT swap "Total QTY" and "Unit rate". "Total QTY" is always the larger order count (50, 250, 70). "Unit rate" is always the price per piece (1.00, 2.00, 47.00). They are in separate columns.
CORRECT EXAMPLE: Row shows Total QTY=50, Unit rate=1.00, Total=50.00 → quantity=50, unit_price=1.00, total_price=50.00
WRONG EXAMPLE: quantity=1, unit_price=1.00, total_price=1.00 — this is wrong, total doesn't match the table

- For "unit": use "Pcs" for pipes/tubes, "Nos" for fittings/couplings/elbows. Do NOT use "kg".
- Be precise with numbers — copy them exactly as shown.

Return ONLY a valid JSON object with this exact structure:
{
  "supplier_name": "string",
  "supplier_contact": "string",
  "supplier_email": "string",
  "supplier_phone": "string",
  "supplier_address": "string",
  "supplier_trn": "string",
  "quotation_number": "string",
  "quotation_date": "YYYY-MM-DD",
  "validity_date": "YYYY-MM-DD",
  "payment_terms": "string",
  "currency": "AED",
  "line_items": [
    {
      "description": "full descriptive string combining all spec columns",
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
Return ONLY the JSON object, no additional text.
""",
            "invoice": """
Extract the following information from this tax invoice document.

Return ONLY a valid JSON object with this exact structure:
{
  "supplier_name": "string",
  "supplier_trn": "string",
  "supplier_address": "string",
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
Return ONLY the JSON object, no additional text.
""",
            "delivery_note": """
Extract the following information from this delivery note.

Return ONLY a valid JSON object with this exact structure:
{
  "delivery_note_number": "string",
  "delivery_date": "YYYY-MM-DD",
  "lpo_reference": "string",
  "supplier_name": "string",
  "received_by": "string",
  "line_items": [
    {
      "description": "string",
      "quantity_delivered": number,
      "unit": "string",
      "condition": "Good"
    }
  ],
  "notes": "string"
}

Return ONLY the JSON object, no additional text.
"""
        }

        prompt = prompts.get(document_type, prompts["supplier_quotation"])

        # Call OpenAI GPT-4 Vision
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{media_type};base64,{image_data}"
                                }
                            },
                            {
                                "type": "text",
                                "text": prompt
                            }
                        ]
                    }
                ],
                max_tokens=4000,
                temperature=0
            )

            response_text = response.choices[0].message.content

            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}') + 1

            if start_idx != -1 and end_idx > start_idx:
                extracted_data = json.loads(response_text[start_idx:end_idx])
                return {"success": True, "data": extracted_data, "raw_response": response_text}
            else:
                return {"success": False, "error": "No JSON found in response", "raw_response": response_text}

        except json.JSONDecodeError as e:
            return {"success": False, "error": f"Failed to parse JSON: {str(e)}", "raw_response": response_text}
        except Exception as e:
            return {"success": False, "error": f"OpenAI API error: {str(e)}", "raw_response": ""}

    def extract_from_text(self, text, document_type='supplier_quotation'):
        """Extract structured data from plain text (e.g. extracted from a PDF).
        Much more accurate than vision for documents with long item descriptions."""

        prompt = f"""You are extracting data from a supplier quotation document.

CRITICAL RULES:
- Copy item descriptions WORD FOR WORD, EXACTLY as written. Do NOT summarize, abbreviate, or shorten them.
- Extract ALL line items — do not skip any.
- For "unit", extract the unit of measure (Pcs, Nos, m, kg, etc.) — separate from the quantity number.
- For quantities, extract only the number (not the unit).
- If a field is not found, use empty string for strings, 0 for numbers.
- Return ONLY a valid JSON object, no other text.

Return this exact JSON structure:
{{
  "supplier_name": "string",
  "supplier_contact": "string",
  "supplier_email": "string",
  "supplier_phone": "string",
  "supplier_address": "string",
  "supplier_trn": "string",
  "quotation_number": "string",
  "quotation_date": "YYYY-MM-DD",
  "validity_date": "YYYY-MM-DD",
  "payment_terms": "string",
  "currency": "AED",
  "line_items": [
    {{
      "description": "FULL verbatim description from document",
      "quantity": number,
      "unit": "string",
      "unit_price": number,
      "total_price": number
    }}
  ],
  "subtotal": number,
  "vat_percent": number,
  "vat_amount": number,
  "total_amount": number,
  "notes": "string"
}}

=== DOCUMENT TEXT ===
{text}
=== END OF DOCUMENT ==="""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4000,
                temperature=0
            )
            response_text = response.choices[0].message.content
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}') + 1
            if start_idx != -1 and end_idx > start_idx:
                extracted_data = json.loads(response_text[start_idx:end_idx])
                return {"success": True, "data": extracted_data, "raw_response": response_text}
            return {"success": False, "error": "No JSON found in response", "raw_response": response_text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def extract_from_voice(self, audio_text, context="general"):
        """Extract structured data from voice-transcribed text

        Args:
            audio_text: Transcribed text from speech
            context: Type of data being entered (quotation/invoice/cheque/etc)
        """

        prompts = {
            "lpo": """
The user spoke this text to create an LPO. Extract the information and return ONLY a valid JSON object:

{
  "supplier_name": "string",
  "lpo_date": "YYYY-MM-DD",
  "expected_delivery_date": "YYYY-MM-DD",
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
The user spoke this text to create a cheque. Extract the information and return ONLY a valid JSON object:

{
  "cheque_number": "string",
  "bank_name": "string",
  "cheque_date": "YYYY-MM-DD",
  "amount": number,
  "supplier_name": "string",
  "notes": "string"
}

Spoken text: """ + audio_text,

            "general": """
The user spoke this text. Extract any structured information and return as JSON.
Identify the type of document (invoice/cheque/lpo/delivery_note) and extract relevant fields.

Spoken text: """ + audio_text
        }

        prompt = prompts.get(context, prompts["general"])

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                max_tokens=2000,
                temperature=0
            )

            response_text = response.choices[0].message.content

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

        except Exception as e:
            return {
                "success": False,
                "error": f"OpenAI API error: {str(e)}",
                "raw_response": ""
            }


# CLI for testing
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python ai_extraction_openai.py <image_path> <supplier_quotation|invoice|delivery_note>")
        sys.exit(1)

    image_path = sys.argv[1]
    doc_type = sys.argv[2]

    extractor = AIExtractor()
    result = extractor.extract_from_image(image_path, doc_type)

    print(json.dumps(result, indent=2))
