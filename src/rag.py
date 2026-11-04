from src.llm_client import ask_llm
from src.vector_store import search

# Bu değerden uzak (büyük) en iyi sonuç = kaynakta cevap yok sayılır.
# Başlangıç tahmini: test sonuçlarına göre ayarlayacağız.
MAX_DISTANCE = 0.55

NOT_FOUND_MESSAGE = "Bu soru için yüklenen ders materyallerinde yeterli bilgi bulamadım."

PROMPT_TEMPLATE = """Sen bir ders asistanısın. Aşağıdaki KAYNAKLAR'ı kullanarak soruyu Türkçe yanıtla.

Kurallar:
- Yalnızca KAYNAKLAR'daki bilgiye dayan. Kendi bilginden ekleme yapma.
- Cevap KAYNAKLAR'da yoksa sadece şunu yaz: "{not_found}"
- Cevabı kısa ve net tut.

KAYNAKLAR:
{context}

SORU: {question}

CEVAP:"""


def answer_question(
    question: str, top_k: int = 3, source_ids: list[str] | None = None
) -> dict:
    """Soruyu yanıtlar. {'answer', 'sources', 'found'} döndürür."""
    hits = search(question, top_k=top_k, source_ids=source_ids)

    # Koruma 1: en iyi sonuç bile çok uzaksa LLM'i çağırma.
    if not hits or hits[0]["distance"] > MAX_DISTANCE:
        return {"answer": NOT_FOUND_MESSAGE, "sources": hits, "found": False}

    context = "\n\n".join(f"[Sayfa {h['page']}]\n{h['text']}" for h in hits)
    prompt = PROMPT_TEMPLATE.format(
        not_found=NOT_FOUND_MESSAGE, context=context, question=question
    )
    answer = ask_llm(prompt).strip()

    # Koruma 2: model kendisi "bulamadım" dediyse bunu işaretle.
    found = NOT_FOUND_MESSAGE not in answer
    return {"answer": answer, "sources": hits, "found": found}
