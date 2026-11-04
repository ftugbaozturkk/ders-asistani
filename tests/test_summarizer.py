import time
from pathlib import Path

from src.pdf_loader import extract_pages
from src.summarizer import summarize_pages


def test_summary():
    pdfs = list(Path("data/pdfs").glob("*.pdf"))
    assert pdfs, "data/pdfs klasöründe PDF yok. PDF'i bu klasöre kopyala."

    pages = extract_pages(str(pdfs[0]))
    start = time.time()
    summary = summarize_pages(
        pages, progress=lambda i, n: print(f"  bölüm {i}/{n} tamam")
    )
    print(f"\nSüre: {time.time() - start:.0f} saniye")
    print("\n" + summary)

    assert len(summary) > 200


if __name__ == "__main__":
    test_summary()
    print("\nTEST GEÇTİ: Özetleme çalışıyor.")