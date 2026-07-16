import os
import re
from datetime import datetime

import pandas as pd
import PyPDF2
import pytesseract
from pdf2image import convert_from_path

VALIDATION_DATA = {
    "Company Name": "Anlagentechnik GmbH",  # Plus-Galvano-
    "Address": "Liebigstr. 2 40764 Langenfeld",  #
    # "Date Range": {"start": "01.01.2024", "end": "31.12.2024"},
    "Date Range": {"start": "2024-01-01", "end": "2024-12-31"},
    "Expected Last Amount": 4522.00,
}

# List of German month names for matching
GERMAN_MONTHS = [
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
]


# Helper function to parse dates dynamically
def parse_date_dynamic(date_str):
    try:
        # Try numeric format (DD.MM.YYYY)
        return datetime.strptime(date_str, "%d.%m.%Y").strftime("%Y-%m-%d")
    except ValueError:
        pass  # Fallback to named month format
    try:
        # Try named month format (e.g., "11. Januar 2025")
        return datetime.strptime(date_str, f"%d. %B %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None  # Return None if both formats fail


# Function to extract text from a single PDF
def extract_text_from_pdf(pdf_path):
    extracted_text = ""

    # Attempt direct text extraction (for text-based PDFs)
    try:
        with open(pdf_path, "rb") as pdf_file:
            reader = PyPDF2.PdfReader(pdf_file)
            for page in reader.pages:
                extracted_text += page.extract_text() or ""  # Extract text
    except Exception as e:
        print(f"Error in text extraction for {pdf_path}: {e}")

    # Fallback to OCR if no text is found
    if not extracted_text.strip():
        print(f"No text found in {os.path.basename(pdf_path)}, falling back to OCR...")
        images = convert_from_path(pdf_path)  # Convert PDF to images
        for img in images:
            extracted_text += pytesseract.image_to_string(img, lang="deu")  # German OCR

    return extracted_text


# Function to parse invoice data
def parse_invoice_data(text):
    # Patterns
    date_patterns = [
        r"\d{2}\.\d{2}\.\d{4}",  # DD.MM.YYYY
        r"\d{1,2}\.\s?(?:%s)\s?\d{4}" % "|".join(GERMAN_MONTHS),  # DD. Month YYYY
    ]
    amount_pattern = (
        r"\d{1,3}(?:\.\d{3})*(?:,\d{2})\s?€"  # Currency amounts like 1.234,56 €
    )
    invoice_number_patterns = [
        r"R E C H N U N G[:\s]*([a-zA-Z0-9-]+)",  # Pattern for spaced "R E C H N U N G:"
        r"Rechnung[:\s]*([a-zA-Z0-9-]+)",  # Pattern for "Rechnung:"
        r"Rechnung Nr[:.\s]*([a-zA-Z0-9-]+)",  # Pattern for "Rechnung Nr.:"
    ]
    address_pattern = r"(?P<street>[\w\s.]+?)\s(?P<number>\d+)\n(?P<postal_code>\d{5})\s(?P<city>[\w\s-]+)"

    # Extract dates from all patterns
    dates = []
    for pattern in date_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        dates.extend(matches)

    # Extract all amounts
    amounts = re.findall(amount_pattern, text)
    amounts_cleaned = [
        float(a.replace(".", "").replace(",", ".").replace("€", "").strip())
        for a in amounts
    ]

    # Simplified company name search
    company_name = (
        VALIDATION_DATA["Company Name"]
        if VALIDATION_DATA["Company Name"] in text
        else "Not Found"
    )

    # Extract address
    address = "Not Found"
    match = re.search(address_pattern, text, re.MULTILINE)
    if match:
        address = f"{match.group('street')} {match.group('number')}\n{match.group('postal_code')} {match.group('city')}"

    # Extract invoice number using multiple patterns
    invoice_number = "Not Found"
    for pattern in invoice_number_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            invoice_number = match.group(1).strip()
            break

    return {
        "Dates": dates,
        "Amounts": amounts_cleaned,
        "Company Name": company_name,
        "Address": address,
        "Invoice Number": invoice_number,
    }


# Function to validate extracted data
def validate_invoice(data):
    # Validate company name and address against known values
    validated_dates = [parse_date_dynamic(date) for date in data["Dates"]]
    validations = {
        "Company Name Valid": data["Company Name"] == VALIDATION_DATA["Company Name"],
        "Address Valid": data["Address"] == VALIDATION_DATA["Address"],
        "Date Range Valid": any(
            date
            and VALIDATION_DATA["Date Range"]["start"]
            <= date
            <= VALIDATION_DATA["Date Range"]["end"]
            for date in validated_dates
        ),
        "Last Amount Valid": (
            (data["Amounts"][-1] == VALIDATION_DATA["Expected Last Amount"])
            if data["Amounts"]
            else False
        ),
        "Invoice Number Found": data["Invoice Number"] != "Not Found",
    }
    return validations


# Process multiple PDF files
def process_invoices(pdf_files):
    results = []

    for pdf_file in pdf_files:
        print(f"\nProcessing file: {os.path.basename(pdf_file)}")
        text = extract_text_from_pdf(pdf_file)
        parsed_data = parse_invoice_data(text)
        validations = validate_invoice(parsed_data)

        # Merge parsed data and validations
        data = {
            "File": os.path.basename(pdf_file),
            "Invoice Number": parsed_data["Invoice Number"],
            "Dates": parsed_data["Dates"],
            "Amounts": parsed_data["Amounts"],
            "Company Name": parsed_data["Company Name"],
            "Address": parsed_data["Address"],
            **validations,
        }
        results.append(data)

    # Convert results to DataFrame for better readability
    df = pd.DataFrame(results)
    return df


# List of PDF files to process
pdf_files = [
    "beispiel_rechnung.pdf",
    "2025_01_Rechnung_PAPANIKANDROS_EnergieKonzept.pdf",
]  # Replace with your test file path

# Run the process and display the results
invoice_data = process_invoices(pdf_files)
print("\nExtracted Invoice Data:")
print(invoice_data)

# Optionally save results to a CSV file
invoice_data.to_csv("validated_invoice_data.csv", index=False)
print("\nData saved to 'validated_invoice_data.csv'")
