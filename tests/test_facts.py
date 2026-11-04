"""Çevrimdışı testler: PDF ve model gerekmez. Çalıştır: python -m tests.test_facts"""
import json
import re

from src.facts import norm, tur
from src.question_generator import generate_questions


def _page(no, bloklar=(), tablolar=()):
    return {
        "page": no,
        "text": " ".join(b[0] for b in bloklar),
        "yapi": {
            "bloklar": [{"metin": m, "kalin": k, "tamami_kalin": False} for m, k in bloklar],
            "tablolar": list(tablolar),
        },
    }


PAGES = [
    _page(1, [
        ("Karma tablo: Anahtarları indekse çevirerek hızlı erişim sağlayan veri yapısıdır.", ["Karma tablo"]),
        ("Yığın: Son giren ilk çıkar ilkesiyle çalışan veri yapısıdır, özyinelemede kullanılır.", ["Yığın"]),
        ("Kuyruk: İlk giren ilk çıkar ilkesiyle çalışan veri yapısıdır, sıra işlemlerinde kullanılır.", ["Kuyruk"]),
        ("Bağlı liste: Elemanların işaretçilerle bağlandığı doğrusal veri yapısıdır, ardışık bellek gerekmez.", ["Bağlı liste"]),
        ("Her sayfayı okuyup kalın yazılanları not edin ve sonra tekrar edin lütfen.", ["kalın yazılanları"]),
    ]),
    _page(2, tablolar=[{
        "baslik": ["Algoritma", "Karmaşıklık", "Not"],
        "satirlar": [
            ["İkili arama", "O(log n)", "Sıralı dizide"],
            ["Doğrusal arama", "O(n)", "Sırasız dizide"],
            ["Birleştirme sıralaması", "O(n log n)", "Kararlı"],
            ["Kabarcık sıralaması", "O(n2)", "Basit"],
        ],
    }]),
]


def sahte_model(prompt, json_schema=None, temperature=0.1):
    """Gerçek model yerine: soru = cümlenin cevapsız hali; doğrulama = cümlenin kendisi."""
    if "SORU:" in prompt:
        sentence = re.search(r"METİN: (.*)\nSORU", prompt, re.S).group(1)
        return json.dumps({"cevap": sentence})
    m = re.search(r"CÜMLE: (.*)\nCEVAP: (.*)", prompt)
    masked = m.group(1).replace(m.group(2), "", 1).strip(" :,.").split()
    return json.dumps({"soru": " ".join(masked[:9]) + " nedir?"})


def test_tur():
    assert tur("19 Mayıs 1919") == "tarih"
    assert tur("Lozan Antlaşması (1923)") == "terim_tarih"
    assert tur("Karma tablo") == "terim"
    assert norm("İSTANBUL") == "istanbul"


def test_questions_are_grounded():
    for seed in range(20):
        qs = generate_questions(PAGES, n=6, seed=seed, ask=sahte_model)
        assert qs, "soru üretilemedi"
        for q in qs:
            opts = q["secenekler"]
            answer = opts[q["dogru_index"]]
            assert len(opts) == 4 and len(set(opts)) == 4
            assert norm(answer) in norm(q["kaynak_alinti"]), (answer, q["kaynak_alinti"])
            assert "kalın yazılanları" not in " ".join(opts)  # üst bilgi cümlesi sorulmaz
            for i, o in enumerate(opts):
                if i != q["dogru_index"]:
                    assert norm(o) not in norm(q["kaynak_alinti"]), (o, q["kaynak_alinti"])


def test_table_question_uses_same_column():
    qs = generate_questions(PAGES, n=8, seed=1, ask=sahte_model)
    for q in qs:
        if q["soru"].startswith("Tabloya göre"):
            cols = [{r[1] for r in PAGES[1]["yapi"]["tablolar"][0]["satirlar"]},
                    {r[2] for r in PAGES[1]["yapi"]["tablolar"][0]["satirlar"]}]
            assert any(set(q["secenekler"]) <= c for c in cols), q["secenekler"]


def test_heal_split_words():
    from src.facts import heal_pages
    pages = [_page(1, [("Eskişehir savaşı büyük bir savaştır ve Eskişehir önemlidir.", [])],
                   [{"baslik": ["A", "B", "C"], "satirlar": [["Eski şehir", "1", "x"]]}])]
    fixed = heal_pages(pages)
    assert fixed[0]["yapi"]["tablolar"][0]["satirlar"][0][0] == "Eskişehir"


if __name__ == "__main__":
    test_heal_split_words()
    test_tur()
    test_questions_are_grounded()
    test_table_question_uses_same_column()
    print("TEST GEÇTİ: soru üretimi çevrimdışı testleri")