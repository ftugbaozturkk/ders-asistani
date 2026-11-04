from src.llm_client import ask_llm

GROUP_CHARS = 6000  # bir model çağrısına verilen en fazla metin
MAX_GROUPS = 20     # bundan uzun doküman şimdilik reddedilir

PART_PROMPT = """Aşağıdaki ders notu bölümünü Türkçe, akıcı bir dille özetle.

Kurallar:
- Yalnızca kaynakta bulunan bilgilere dayan; çıkarım veya dış bilgi ekleme.
- Madde işareti ya da numaralı liste kullanma. Konuları birbirine bağlayan kısa paragraflar yaz.
- Her paragrafta bir ana konuya odaklan; tekrarları birleştir, önemsiz ayrıntıları çıkar.
- Önemli tarihleri, kişi ve olay adlarını, tanımları ve neden-sonuç ilişkilerini doğru koru.
- Tablodaki bilgileri aktarırken farklı satırların bilgilerini birbirine karıştırma.
- Kaynak bir soru listesi veya cevap anahtarıysa yalnızca "Bu bölüm sorulardan oluşuyor." yaz.
- Başlık ekleme; doğrudan özet paragraflarıyla başla.

METİN (sayfa {first}-{last}):
{text}

ÖZET:"""

FINAL_PROMPT = """Aşağıda bir ders notunun bölüm özetleri var. Bunları birleştirip tüm doküman için Türkçe, akıcı ve düzenli bir özet yaz.

Kurallar:
- Yalnızca bölüm özetlerindeki bilgilere dayan; dış bilgi veya yeni çıkarım ekleme.
- Madde işareti ya da numaralı liste kullanma. Konuları birbirine bağlayan paragraflar yaz.
- Gerekirse kısa konu başlıkları kullan, her başlığın altında paragraf biçiminde anlat.
- Tarihleri, kişi ve olay adlarını koru; tekrarları birleştir.
- Kronoloji ve neden-sonuç ilişkilerini anlaşılır biçimde kur.

BÖLÜM ÖZETLERİ:
{parts}

GENEL ÖZET:"""


def _group_pages(pages: list[dict], max_chars: int) -> list[list[dict]]:
    """Sayfaları, toplam karakteri max_chars'ı geçmeyecek gruplara ayırır.

    Tek bir sayfa sınırı aşıyorsa metin parçalanır; içeriğin sonu atılmaz.
    """
    groups, current, size = [], [], 0
    for p in pages:
        text = p["text"]
        for start in range(0, len(text), max_chars):
            part = text[start : start + max_chars]
            if current and size + len(part) > max_chars:
                groups.append(current)
                current, size = [], 0
            current.append({"page": p["page"], "text": part})
            size += len(part)
    if current:
        groups.append(current)
    return groups


def summarize_pages(
    pages: list[dict], progress=None, birlestir: bool = False
) -> str:
    """Sayfa listesini özetler; progress(i, toplam) bölüm çağrısı öncesi/sonrası çalışır."""
    groups = _group_pages(pages, GROUP_CHARS)
    if len(groups) > MAX_GROUPS:
        raise ValueError(
            f"Doküman çok uzun ({len(groups)} bölüm). Şimdilik en fazla "
            f"{MAX_GROUPS} bölüm (yaklaşık 60 sayfa) özetlenebiliyor."
        )

    part_summaries = []
    for i, group in enumerate(groups, start=1):
        first, last = group[0]["page"], group[-1]["page"]
        text = "\n\n".join(f"[Sayfa {p['page']}]\n{p['text']}" for p in group)
        prompt = PART_PROMPT.format(first=first, last=last, text=text)
        if progress:
            progress(i, len(groups))
        summary = ask_llm(prompt).strip()
        part_summaries.append(f"**Sayfa {first}-{last}**\n{summary}")
        if progress:
            progress(i, len(groups))

    if len(part_summaries) == 1 or not birlestir:
        return "\n\n".join(part_summaries)

    joined = "\n\n".join(part_summaries)
    return ask_llm(FINAL_PROMPT.format(parts=joined)).strip()
