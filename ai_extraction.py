"""
AI-assisted data extraction from documents using Claude Opus 4.5
Extracts structured data from quotations, invoices, and other documents
"""

import os
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

    def extract_from_image(self, image_path, document_type):
        """Extract data from image file"""
        # Read and encode image
        with open(image_path, 'rb') as f:
            image_data = base64.standard_b64encode(f.read()).decode('utf-8')

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
        """Core extraction logic"""

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
"""
        }

        prompt = prompts.get(document_type, prompts["quotation"])

        # Call Claude with vision
        message = self.client.messages.create(
            model=self.model,
            max_tokens=4000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ],
                }
            ],
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

        message = self.client.messages.create(
            model=self.model,
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
