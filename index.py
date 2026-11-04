"""Index PDFs from data/pdfs for use with the command-line assistant."""
import hashlib
from pathlib import Path

from src.chunker import chunk_pages
from src.pdf_loader import extract_pages
from src.vector_store import build_index


ROOT = Path(__file__).resolve().parent
PDF_DIR = ROOT / "data" / "pdfs"


def main() -> None:
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"PDF bulunamadı: {PDF_DIR}")
        return

    for pdf in pdfs:
        content = pdf.read_bytes()
        source_id = hashlib.sha256(content).hexdigest()[:16]
        chunks = chunk_pages(extract_pages(str(pdf)))
        for chunk in chunks:
            chunk["id"] = f"{source_id}_{chunk['id']}"
        count = build_index(chunks, source_id=source_id)
        print(f"{pdf.name}: {count} parça indekslendi.")


if __name__ == "__main__":
    main()
