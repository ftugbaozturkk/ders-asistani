import re
from pathlib import Path

import pymupdf as fitz  # PyMuPDF


def _table_text(table) -> str:
    """Tabloyu markdown'a çevirir; HTML etiketlerini ve kalın işaretlerini temizler."""
    md = table.to_markdown().replace("<br>", " ").replace("**", " ")
    return re.sub(r"[ \t]+", " ", md).strip()


def _find_tables(page) -> list:
    try:
        return page.find_tables().tables
    except Exception:
        return []


def _page_text(page, tables=None) -> str:
    """Sayfa metnini döndürür; tabloları satır satır (markdown) korur."""
    if tables is None:
        tables = _find_tables(page)

    if not tables:
        return page.get_text().strip()

    boxes = [fitz.Rect(t.bbox) for t in tables]
    parts, emitted = [], set()

    for block in page.get_text("blocks"):
        if block[6] != 0:  # 0 = metin bloğu, diğerleri görsel
            continue
        rect = fitz.Rect(block[:4])
        hit = next((i for i, b in enumerate(boxes) if rect.intersects(b)), None)
        if hit is None:
            parts.append(block[4].strip())
        elif hit not in emitted:
            parts.append(_table_text(tables[hit]))
            emitted.add(hit)

    for i, table in enumerate(tables):
        if i not in emitted:
            parts.append(_table_text(table))

    return "\n\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Yapı çıkarma (soru üretimi için): kalın yazılar ve tablo hücreleri
# ---------------------------------------------------------------------------

def _is_bold(span) -> bool:
    return bool(span["flags"] & 16) or "bold" in span["font"].lower()


def _clean_cell(text: str) -> str:
    return " ".join(text.split())


def _page_structure(page, found=None) -> dict:
    """Sayfadaki tabloları (hücre hücre) ve kalın yazı içeren metin bloklarını çıkarır.

    Dönen sözlük:
      tablolar: [{'baslik': [...], 'satirlar': [[hücre, ...], ...]}]
      bloklar:  [{'metin': str, 'kalin': [kalın parçalar], 'tamami_kalin': bool}]
    """
    if found is None:
        found = _find_tables(page)

    tables, boxes = [], []
    for t in found:
        rows = []
        for r in t.rows:
            cells = []
            for c in r.cells:
                cells.append(
                    "" if c is None
                    else _clean_cell(page.get_text("text", clip=fitz.Rect(c)))
                )
            rows.append(cells)
        if len(rows) >= 2:
            tables.append({"baslik": rows[0], "satirlar": rows[1:]})
        boxes.append(fitz.Rect(t.bbox))

    blocks = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        if any(fitz.Rect(block["bbox"]).intersects(b) for b in boxes):
            continue
        text, runs, current = "", [], None  # current = kalın parçanın başlangıcı
        for line in block["lines"]:
            for span in line["spans"]:
                piece = span["text"]
                if not piece.strip():
                    text += piece
                    continue
                if _is_bold(span):
                    if current is None:
                        current = len(text)
                else:
                    if current is not None:
                        runs.append(text[current:].strip())
                        current = None
                text += piece
            text += " "
            # satır sonunda kalın parça devam ediyorsa bir sonraki satırla birleşir
        if current is not None:
            runs.append(text[current:].strip())
        text = " ".join(text.split())
        runs = [" ".join(r.split()) for r in runs if r.strip()]
        if not text:
            continue
        full_bold = bool(runs) and len(" ".join(runs)) >= 0.85 * len(text)
        # PDF'te satır kırılması yüzünden bölünmüş cümle devamını önceki bloğa ekle
        continuation = text[0].islower() or text[0] in "(,;" or (len(text) < 25 and not full_bold)
        if blocks and continuation and not blocks[-1]["tamami_kalin"]:
            prev = blocks[-1]
            prev["metin"] += " " + text
            prev["kalin"] += runs
        else:
            blocks.append({"metin": text, "kalin": runs, "tamami_kalin": full_bold})
    return {"tablolar": tables, "bloklar": blocks}


def extract_pages(pdf_path: str) -> list[dict]:
    """Her sayfa için {'page', 'text', 'yapi'} döndürür.

    'text' RAG ve özet içindir (tablolar markdown). 'yapi' soru üretimi içindir.
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF bulunamadı: {path}")

    pages = []
    with fitz.open(path) as doc:
        for index, page in enumerate(doc, start=1):
            found = _find_tables(page)
            text = _page_text(page, found)
            if text:
                pages.append({"page": index, "text": text, "yapi": _page_structure(page, found)})

    if not pages:
        raise ValueError(
            "PDF'ten metin çıkarılamadı. Dosya taranmış bir görüntü olabilir (OCR gerekir)."
        )
    return pages