"""Gerçek PDF'inle soru kalitesini gözle kontrol et.

Çalıştır (proje kökünde, (.venv) aktifken):  python -m tests.test_question_generator
İstersen soru sayısı ve seed ver:             python -m tests.test_question_generator 10 3
"""
import sys
from pathlib import Path

from src.pdf_loader import extract_pages
from src.question_generator import generate_questions


def main(n: int = 8, seed: int | None = None) -> None:
    pdfs = list(Path("data/pdfs").glob("*.pdf"))
    assert pdfs, "data/pdfs klasöründe PDF yok."
    pages = extract_pages(str(pdfs[0]))
    stats: dict = {}
    questions = generate_questions(pages, n=n, seed=seed, stats=stats)

    print(f"Dosya: {pdfs[0].name} | {len(pages)} sayfa | {stats}")
    for i, q in enumerate(questions, 1):
        print(f"\n{i}. [Sayfa {q['sayfa']}] {q['soru']}")
        for j, option in enumerate(q["secenekler"]):
            print(f"   {'*' if j == q['dogru_index'] else ' '} {option}")
        print(f"   kaynak: {q['kaynak_alinti'][:160]}")

    assert questions, "Soru üretilemedi (PDF'te kalın yazı/tablo/tanım cümlesi yok olabilir)."
    for q in questions:
        answer = q["secenekler"][q["dogru_index"]]
        assert len(set(q["secenekler"])) == len(q["secenekler"])
        assert answer.lower()[:4] in q["kaynak_alinti"].lower() or any(c.isdigit() for c in answer)


if __name__ == "__main__":
    args = [int(a) for a in sys.argv[1:3]]
    main(*args)
    print("\nTEST GEÇTİ")