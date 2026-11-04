"""PDF'ten çoktan seçmeli soru üretir.

Önceki sürüm soruyu, yanlış şıkları ve alıntıyı 7B modele yazdırıyordu; model anlamsız
sorular, yanlış cevaplar ve ipucu veren şıklar üretiyordu. Bu sürümde:
  - Soru konusu yazarın vurguladığı bilgiden (kalın yazı, tablo) seçilir.
  - Doğru cevap PDF'teki hücre/kalın ifadenin kendisidir; yanlış olamaz.
  - Yanlış şıklar aynı belgeden, aynı türden (tarih, terim, ...) alınır.
  - Soru, kaynak cümleyi mekanik biçimde kopyalamadan doğal Türkçeyle kurulur.
Model yalnızca soru cümlesini düzgün Türkçe ile yazmak için kullanılır; yazdığı soru
sıkı denetimden geçer (cevabı sızdırmıyor mu, yeni bilgi uyduruyor mu, aynı cevabı
buluyor mu). Denetimi geçemeyen bilgi için soru sorulmaz.
"""
import json
import math
import random
import re

from src.facts import (
    build_causal_facts,
    build_definition_facts,
    build_facts,
    heal_pages,
    norm,
    pool_from_facts,
    tur,
)
from src.llm_client import ask_llm

BLANK = "_____"

STEM_SCHEMA = {
    "type": "object",
    "properties": {"soru": {"type": "string"}},
    "required": ["soru"],
}
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"cevap": {"type": "string"}},
    "required": ["cevap"],
}
PERSON_TITLE_WORDS = {"paşa", "bey", "efendi", "ağa", "hoca"}

STEM_PROMPT = """Kaynak cümledeki bilgiye dayanarak, cevabı TAM OLARAK verilen CEVAP olan doğal bir Türkçe sınav sorusu yaz.

Kurallar:
- Cümleyi kopyalayıp boşluk doldurma sorusuna çevirme; bilgiyi doğal soru biçiminde yeniden kur.
- Kısa, açık ve günlük Türkçede doğal bir soru yaz. Konuyu/özneyi önce söyle, ardından soru ifadesini kullan.
- "Ne zaman halifelik kaldırıldı?" gibi soru ifadesiyle başlayıp özneyi sonradan getiren dizilişlerden kaçın.
- Soru ifadesini yüklemden önce kullan: "İngilizler İstanbul'u ne zaman işgal etti?" gibi; yüklemden sonra "ne zaman" ekleme.
- Aynı anlamı tekrarlama; "Ankara'nın başkent ilan edildiği tarih ne zaman?" gibi kalıplar kurma.
- Soruda yalnızca kaynakta bulunan bilgiye yer ver; yeni isim, tarih veya ayrıntı ekleme.
- CEVAP'ı soruda yazma.
- Soruyu tek başına anlaşılır kur; "metin", "cümle", "yukarıdaki", "boşluk" gibi kaynak dışı göndermeler kullanma.
- Cevabın türüne uygun soru kalıbı seç (tarih için "ne zaman", kişi için "kim", kavram için "nedir/hangisidir").
- Soruyu göndermeden önce Türkçesini düzelt: yazım, ek ve fiil çekimlerini kontrol et. Özel ad veya kavramı fiil gibi çekimleme ("Takrir-i Sükûn takrirlendi" gibi).
- Soru işaretiyle bitir, tek cümle olsun.

CÜMLE: {sentence}
CEVAP: {answer}"""

TABLE_STEM_PROMPT = """Tablo satırındaki bilgiye dayanarak, cevabı TAM OLARAK verilen CEVAP olan doğal bir Türkçe sınav sorusu yaz.

Kurallar:
- CÜMLE'deki tablo satırına dayan. KONU'yu sorunun öznesi yap; SÜTUN'daki bilgiyi doğal biçimde sor.
- CEVAP BİÇİMİNE uygun sor: yalnızca yıl ise "hangi yıl", tek tarih ise "hangi tarihte/ne zaman", aralık ise "hangi tarihler arasında" diye sor. SÜTUN adından farklı bir biçim çıkıyorsa CEVAP BİÇİMİNE uy.
- Konunun türüne uygun fiil seç: antlaşma imzalanır, kurum kurulur, savaş yapılır/sürer, gelişme gerçekleşir. CÜMLE'deki Not bilgisini fiili belirlemek için kullanabilirsin.
- KONU TÜRÜ alanındaki fiili kullan; antlaşmalar için "imzalandı" anlamındaki doğal kalıbı seç, "kuruldu" deme.
- Soruyu göndermeden önce Türkçesini düzelt: yazım, ek ve fiil çekimlerini kontrol et; özel adı fiile dönüştürme.
- Emin değilsen yeni bir olay iddiası kurma; konuyu özne yapıp doğrudan ve tarafsız bir soru sor.
- "Tabloya göre", tırnak içindeki başlıklar ve "değeri nedir?" gibi ham tablo ifadelerini soruya taşıma.
- Yalnızca CÜMLE'de bulunan bilgiye dayan; yeni isim, tarih veya ayrıntı ekleme. CEVAP'ı soruda yazma.
- Tek bir soru sor; cevabın kapsamadığı tarihleri veya ikinci bir bilgiyi ayrıca sorma.
- Konuyu önce söyle, soru ifadesini sonra getir. "Ne zaman X oldu?" diye başlama; "X ne zaman oldu?" biçimini kullan.
- "X'in hangi yılda gerçekleştiği?" gibi tamamlanmamış yan cümle kurma. Tek, kısa ve doğal bir soru yaz; soru işaretiyle bitir.

KONU: {item}
KONU TÜRÜ: {item_kind}
SÜTUN: {column}
CEVAP BİÇİMİ: {answer_form}
CÜMLE: {sentence}
CEVAP: {answer}"""

CAUSAL_STEM_PROMPT = """Aşağıdaki açık neden-sonuç ilişkisinden, cevabı TAM OLARAK verilen CEVAP olan kısa ve doğal bir Türkçe soru yaz.

İLİŞKİ: {sentence}
SORULAN: {target}
NEDEN: {cause}
SONUÇ: {effect}

Kurallar:
- SORULAN sonuç ise nedenin neye yol açtığını sor. SORULAN neden ise sonucun hangi sebepten kaynaklandığını sor.
- Konuyu önce söyle; "ne oldu?", "hangi sonuç doğdu?" veya "neye yol açtı?" gibi doğrudan soru kalıpları kullan.
- Yalnızca İLİŞKİ'de bulunan bilgiye dayan; cevabı soruda verme.
- Yan cümleyle bitirme. Tek, tamamlanmış ve doğal bir soru yaz; aynı anda hem nedeni hem sonucu sorma.

CÜMLE: {sentence}
CEVAP: {answer}"""

VERIFY_PROMPT = """Yalnızca aşağıdaki metne bakarak soruyu en kısa haliyle yanıtla. Metinde yoksa "yok" yaz.

Soruda sorulan ilişkinin yönünü dikkatle izle. "A yerine B getirildi" ifadesinde
değiştirilen A, onun yerine gelen B'dir; bunları birbirinin cevabı sayma.
Soru, kaynakta açıkça yazan bilgi türünü ve rolünü doğru hedeflemiyorsa "yok" yaz.

METİN: {sentence}
SORU: {question}"""

BANNED_RE = re.compile(r"\b(boş|aşağıdaki|yukarıdaki|metin|metne|cümle|parça|şık|seçenek|ifade)", re.I)
ALLOWED_CHARS_RE = re.compile(r"^[A-Za-zÇĞİÖŞÜçğıöşü0-9\s«»\"'’‘“”()\-–—,.:;?/%&]+$")
QUESTION_WORDS = set("""hangi hangisi hangileri nedir nelerdir kimdir kimdi neydi nerede nereye nereden
ne zaman neden nasıl kaç tarihte yılında yıl tarih adı adlı denir denilir ismi ismini hangisidir
kimlerdir nelerdir olarak""".split())


def _length_giveaway(options: list[str], index: int) -> bool:
    """Doğru şık diğerlerinden belirgin biçimde uzunsa ipucu verir."""
    longest_other = max(len(o) for i, o in enumerate(options) if i != index)
    return len(options[index]) > 25 and len(options[index]) > 1.4 * longest_other


def _shape(text: str) -> tuple:
    """Şıkların biçim benzerliği: rakam var mı, parantez var mı, kaç sözcük."""
    digit = bool(re.search(r"\d", text))
    year_only = bool(re.fullmatch(r"\d{3,4}", text.strip()))
    return (digit, "(" in text, year_only, 0 if digit else min(len(text.split()), 4))


def _pattern(text: str) -> str:
    """"20 Temmuz 1936" -> "9 a 9", "1936–1939" -> "9–9": şık kalıbı."""
    return re.sub(r"[^\W\d_]+", "a", re.sub(r"\d+", "9", text.strip()))


def _person_name_tokens(text: str) -> set[str]:
    return {
        word for word in re.findall(r"[a-zçğıöşü]+", norm(text))
        if len(word) > 2 and word not in PERSON_TITLE_WORDS
    }


def _likely_person_name(text: str) -> bool:
    clean = re.sub(r"\s*\([^)]*\)", "", text).strip()
    words = clean.split()
    non_person_terms = {
        "antlaşma", "antlaşması", "kongresi", "genelgesi", "kurumu", "cemiyeti",
        "fırkası", "kanunu", "planı", "meclisi", "müfettişi", "muharebesi",
        "savaşı", "sözleşmesi", "ilkesi", "devleti", "cephe", "cephesi", "paktı",
        "müzesi", "ada", "emirleri", "tedrisat", "tevhid", "milliye", "milli", "millî",
    }
    return (
        2 <= len(words) <= 4
        and words[0].rstrip(".").lower() not in {"i", "ii", "iii", "iv"}
        and not any(w.rstrip(".,:;").lower() in {"on", "iki", "üç", "bir"} for w in words)
        and all(w[0].isupper() for w in words if w[0].isalpha())
        and not any(w.rstrip(".,:;").lower() in non_person_terms for w in words)
    )


def _same_person_alias(left: str, right: str) -> bool:
    left_words = {word for word in re.findall(r"[a-zçğıöşü]+", norm(left))}
    right_words = {word for word in re.findall(r"[a-zçğıöşü]+", norm(right))}
    person_like = bool((left_words | right_words) & PERSON_TITLE_WORDS)
    return person_like and bool(_person_name_tokens(left) & _person_name_tokens(right))


def _causal_option_group(text: str) -> str:
    if re.search(r"\d", text):
        return "numeric"
    if _kind(text) == "terim_ozel":
        return "proper"
    if re.search(
        r"\b(?:istedi|çekildi|güçlendi|çıktı|kaldırdı|kaldırıldı|verdi|başladı|"
        r"durdu|oldu|edildi|kuruldu|aldı|bırakıldı|tanındı|katıldı)\s*[.!?]*$",
        norm(text),
    ):
        return "clause"
    return "phrase"


def _answer_family(text: str) -> str | None:
    """Reject distractors from a clearly different subject or answer class."""
    t = norm(text)
    if t.strip() in {"kahraman", "gazi", "şanlı", "sanlı", "sanli"}:
        return "city_title"
    if re.search(r"\b(?:şehirlere unvan|sehir|maraş|maras|antep|urfa)\b", t) \
            and re.search(r"\b(?:kahraman|gazi|şanlı|sanli)\b", t):
        return "city_title"
    if re.search(r"\b(?:zırhlı|zirhli|gemisi|gemi|zırhlısında|zirhlisinda)\b", t):
        return "ship"
    if re.search(r"\b(?:müze|muze|saray|anıtkabir|anitkabir|cenaze)\b", t):
        return "place"
    if re.search(r"\b(?:ateşkes|ateskes|mütareke|mutareke)\b", t):
        return "ceasefire"
    if re.search(r"\b(?:ittifak|itilaf|balkan antantı|sadabat paktı)\b", t):
        return "alliance"
    if re.search(r"\b(?:antlaşma|antlasma|anlaşma|anlasma|pakt|sözleşme|sozlesme|antant)\b", t):
        return "agreement"
    if re.search(r"\b(?:ateşkes hattı|ateskes hatti|sınır hattı|sinir hatti|hat)\b", t):
        return "line"
    if re.search(r"\b(?:kanunu|kanunları|kanunu'nun)\b", t):
        return "law"
    if re.search(r"\b(?:mecelle|hukuk|laikleşme|laiklesme|nikâh|nikah|miras|boşanma|bosanma|velayet)\b", t):
        return "law"
    if re.search(r"\b(?:takvim|ölçü sistemi|saat sistemi)\b", t):
        return "calendar"
    if re.search(r"\b(?:kurumu|üniversite|darülfünun|maarif|eğitim|harf|dil kurumu|tarih kurumu)\b", t):
        return "education_culture"
    if re.search(r"\b(?:ekonomi|ekonomik|sanayi|banka|para politikası|tarım|ticaret)\b", t):
        return "economy"
    if re.search(r"\b(?:halkçılık|cumhuriyetçilik|milliyetçilik|devletçilik|inkılapçılık|laiklik)\b", t):
        return "principle"
    if re.search(r"\b(?:barışla çöz|barisla coz|barış yoluyla|baris yoluyla)\b", t):
        return "peaceful_resolution"
    if re.search(r"\b(?:savaş|savas|muharebe|cephe)\b", t):
        return "battle"
    if re.search(r"\b(?:ordu|komutan|asker|tabur|alay)\b", t):
        return "military"
    if re.search(r"\b(?:kongre|genelge|cemiyet|fırka|firka|meclis|kurultay)\b", t):
        return "political_event_or_body"
    return None


def _distractors(fact: dict, pool: list[str], rng: random.Random, k: int = 3) -> list[str] | None:
    answer, kind = fact["cevap"], fact["tur"]
    a, context = norm(answer), norm(fact["baglam"])
    if fact["kind"] == "causal":
        candidates = fact.get("havuz", [])
    elif fact["kind"] == "tablo":
        candidates = fact.get("havuz", [])
    else:
        # Use the document-wide pool only after semantic-family filtering below;
        # page locality by itself produced distractors from unrelated subjects.
        candidates = fact.get("havuz") or pool

    def usable(d: str) -> bool:
        nd = norm(d)
        # Capitalization alone marks all of these as "proper terms"; keep
        # actual people out of concept/role/label questions.
        if _likely_person_name(d) != _likely_person_name(answer):
            return False
        family_map = fact.get("family_map", {})
        answer_family = family_map.get(a) or _answer_family(answer + " " + fact["baglam"])
        distractor_family = family_map.get(nd) or _answer_family(d)
        if fact["kind"] != "causal" and answer_family and answer_family != distractor_family:
            return False
        if not re.search(r"\d", answer) and abs(len(d.split()) - len(answer.split())) > 2:
            return False
        if _kind(answer) == "terim_ozel" and _kind(d) == "terim_ozel":
            if _same_person_alias(a, nd):
                return False
        in_context_ok = fact["kind"] == "causal" or nd not in context
        return bool(nd) and nd != a and in_context_ok and a not in nd and nd not in a

    chosen, seen = [], {a}
    same = [d for d in candidates if usable(d) and (fact["kind"] in ("tablo", "tanim", "causal") or
                                                    _kind(d) == kind)]
    if fact["kind"] == "causal" and not fact.get("chain_options"):
        group = _causal_option_group(answer)
        same = [d for d in same if _causal_option_group(d) == group]
    shape = _shape(answer)
    if re.search(r"\d", answer):
        # sayı/tarih cevabında yalnızca aynı kalıptaki şıklar ("9 a 9" gibi); eksikse yıl kaydırma
        cls = _pattern(answer)
        same = [d for d in same if _pattern(d) == cls]
        shape = None
    if shape is not None:  # sayı içermeyen cevap: sözcük sayısı ve parantez benzerliği
        word_tolerance = 2 if fact["kind"] == "causal" else 1
        same = [d for d in same if _shape(d)[:3] == shape[:3]
                and abs(_shape(d)[3] - shape[3]) <= word_tolerance]
    # Prefer candidates whose source facts discuss the same local subject.
    # Page-local pools are still kept broad enough to work for short notes.
    rng.shuffle(same)
    same.sort(key=lambda d: abs(len(d) - len(answer)))
    chosen, seen = [], {a}
    for d in same[:max(k * 3, 8)]:
        if norm(d) not in seen and not any(_same_person_alias(d, prior) for prior in chosen):
            chosen.append(d)
            seen.add(norm(d))
    rng.shuffle(chosen)
    chosen = chosen[:k]
    return chosen if len(chosen) == k else None


def _kind(text: str) -> str:
    from src.facts import tur
    return tur(text)


def _stem5(word: str) -> str:
    return norm(word)[:5]


def _words(text: str, minimum: int = 4) -> list[str]:
    return [w for w in re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü0-9]+", text) if len(w) >= minimum]


def _full_sentence(fact: dict) -> str:
    return fact["govde"].replace(BLANK, fact["cevap"], 1)


def _complete_sentence(fact: dict) -> bool:
    """Soruya dönüştürmeye değecek kadar tam bir cümle mi?"""
    s = _full_sentence(fact)
    return len(s.split()) >= 6 and not s.rstrip().endswith(":") and not re.search(r"[≠=→≈↔⇒]", s)


def _stem_problem(question, fact: dict, source: str | None = None) -> str | None:
    """Modelin yazdığı soru cümlesinde sorun varsa nedenini, yoksa None döndürür."""
    if not isinstance(question, str):
        return "metin değil"
    q = " ".join(question.split())
    if not (15 <= len(q) <= 180) or not q.endswith("?") or q.count("?") != 1:
        return "biçim"
    if not ALLOWED_CHARS_RE.match(q):
        return "yabancı karakter"
    if BANNED_RE.search(norm(q)):
        return "yasaklı sözcük"
    nq, na = norm(q), norm(fact["cevap"])
    if re.search(r"\b(?:kanullar|takrirlendi|takrillendi)\b", nq):
        return "yazım veya çekim hatası"
    if fact["kind"] == "tablo" and fact.get("item_kind") == "antlaşma" \
            and re.search(r"\b(?:kuruldu|kurulduğu|oluşturuldu|yapıldı)\b", nq):
        return "antlaşma fiili"
    if na in nq:
        return "cevap soruda"
    if fact["kind"] in ("tablo", "causal") and re.search(r"\bve\b", nq):
        return "birden fazla bilgi sorulmuş"
    numbers = re.findall(r"\d{3,}", fact["cevap"])
    if numbers and any(n in q for n in numbers):
        return "cevap soruda"
    date_question = re.search(r"\b(?:ne zaman|hangi yıl|hangi yılda|hangi tarihte|hangi tarihler arasında)\b", norm(q))
    answer_has_date = bool(re.search(r"\d{3,4}", fact["cevap"]))
    if date_question and not answer_has_date:
        return "cevapta olmayan tarih sorusu"
    if fact["kind"] == "causal":
        asks_cause = bool(re.search(r"\b(?:neden|hangi sebeple|hangi nedenle)\b", norm(q)))
        asks_result = bool(re.search(r"\b(?:ne oldu|hangi sonuç|neye yol açtı)\b", norm(q)))
        if fact["causal_target"] == "sonuç" and asks_cause:
            return "sonuç yerine neden sorulmuş"
        if fact["causal_target"] == "neden" and asks_result:
            return "neden yerine sonuç sorulmuş"
    if re.match(r"^(ne zaman|hangi tarihte|hangi yılda|hangi yıl)\b", norm(q)):
        return "soru ifadesi öznenin önünde"
    if re.search(r"\b(?:gerçekleştiği|imzalandığı|kaldırıldığı|kabul edildiği|kurulduğu|yaptığı|girdiği|katıldığı|başladığı|bittiği)\??$", norm(q)):
        return "tamamlanmamış yan cümle"
    if re.search(r"\bkimydi\??$", norm(q)):
        return "sözdizimi"
    if re.search(r"\b[\w]+['’](?:nun|nün|nın|nin)\s+tarihi nedir\??$", norm(q)):
        return "yapay iyelik kalıbı"
    answer_is_range = bool(re.search(r"\d\s*[–—-]\s*\d", fact["cevap"]))
    asks_for_range = bool(re.search(r"\b(?:hangi|ne) tarihler arasında\b", norm(q)))
    if fact["kind"] == "tablo" and answer_is_range != asks_for_range:
        return "cevap biçimiyle uyuşmayan soru"
    if fact["kind"] == "tablo" and re.fullmatch(r"\d{3,4}", fact["cevap"].strip()):
        if re.search(r"\b(?:ne zaman|hangi tarihte)\b", norm(q)):
            return "yıl cevabına uygun olmayan soru"
    if fact["kind"] == "tablo" and re.search(r"\d{3,4}", fact["cevap"]) \
            and not re.fullmatch(r"\d{3,4}", fact["cevap"].strip()) \
            and re.search(r"\bhangi yıl\b", norm(q)):
        return "tarih cevabına uygun olmayan soru"
    if re.search(
        r"\b(?:etti|ettiler|çıktı|kurdu|kuruldu|kaldırıldı|imzaladı|kabul edildi|"
        r"ilan edildi|başladı|bitti|gitti|taşındı)\s+ne zaman\??$",
        norm(q),
    ):
        return "soru ifadesi yüklemden sonra"
    if re.search(r"\b(?:edildiğinde|olduğunda|gerçekleştiğinde) ne zaman\b", norm(q)):
        return "zaman kalıbı bozuk"
    if re.search(r"\btarihi ne zaman\??$", norm(q)):
        return "tekrarlı soru ifadesi"
    sentence_stems = {_stem5(w) for w in _words(source or _full_sentence(fact), 3)}
    content = [w for w in _words(q, 5) if norm(w) not in QUESTION_WORDS]
    if len(content) < 2:
        return "çok kısa"
    grounded = sum(1 for w in content if _stem5(w) in sentence_stems)
    # Sözcük eşleşmesini tek başına anlam denetimi sayma. Asıl cevap kontrolü,
    # hemen ardından kaynak cümle üzerinde çalışan doğrulama çağrısında yapılır.
    if grounded < 2 or grounded < 0.5 * len(content):
        return "yeni bilgi"
    return None


def _same_answer(reply, answer: str) -> bool:
    if not isinstance(reply, str):
        return False
    r = norm(reply)
    tokens = [t for t in re.findall(r"[a-zçğıöşü0-9]+", norm(answer)) if len(t) >= 2]
    return bool(tokens) and all(t[:5] in r for t in tokens)


def _parse(raw, key: str):
    try:
        return json.loads(raw).get(key)
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None


def _natural_stem(
    fact: dict, ask, why: dict, attempts: int = 2, exclude_questions=None
) -> str | None:
    """Kaynağı doğal soruya çevirtir, denetler ve cevabı kaynakta doğrular."""
    sentence = fact["baglam"] if fact["kind"] in ("tablo", "causal") else _full_sentence(fact)
    if fact["kind"] == "causal":
        prompt_template = CAUSAL_STEM_PROMPT
        prompt_values = {
            "sentence": sentence,
            "target": fact["causal_target"],
            "cause": fact["cause"],
            "effect": fact["effect"],
            "answer": fact["cevap"],
        }
    elif fact["kind"] == "tablo":
        prompt_template = TABLE_STEM_PROMPT
        answer = fact["cevap"].strip()
        if re.search(r"\d\s*[–—-]\s*\d", answer):
            answer_form = "tarih aralığı"
        elif re.fullmatch(r"\d{3,4}", answer):
            answer_form = "yalnızca yıl"
        elif re.search(r"\d{3,4}", answer):
            answer_form = "tam tarih"
        else:
            answer_form = "tarih veya yıl değil; cevabın türünü doğrudan sor"
        prompt_values = {
            "sentence": sentence,
            "item": fact.get("table_item", ""),
            "item_kind": fact.get("item_kind", "olay/konu"),
            "column": fact.get("table_column", ""),
            "answer_form": answer_form,
            "answer": fact["cevap"],
        }
    else:
        prompt_template = STEM_PROMPT
        prompt_values = {"sentence": sentence, "answer": fact["cevap"]}
    retry_hint = ""
    previous = ["- " + q for q in (exclude_questions or [])[-12:]]
    if previous:
        retry_hint = "Önceki quizlerde sorulmuş kalıplar, bunları tekrar etme:\n" + "\n".join(previous)
    for _ in range(attempts):
        try:
            prompt = prompt_template.format(**prompt_values)
            if retry_hint:
                prompt += "\nDÜZELTME: " + retry_hint
            raw = ask(prompt,
                      json_schema=STEM_SCHEMA, temperature=0.4)
        except Exception:
            why["model_hatasi"] = why.get("model_hatasi", 0) + 1
            return None
        question = _parse(raw, "soru")
        problem = _stem_problem(question, fact, source=sentence)
        if problem:
            why[problem] = why.get(problem, 0) + 1
            hints = {
                "soru ifadesi öznenin önünde": "Özneyi başa al; ne zaman/hangi yıl ifadesini yüklemden hemen önce kullan.",
                "soru ifadesi yüklemden sonra": "Ne zaman ifadesini yüklemden önce getir.",
                "tamamlanmamış yan cümle": "-dığı/-diği ile biten yan cümle kurma; çekimli fiille tamamlanan doğrudan soru yaz.",
                "sözdizimi": "Kimdi? biçimini kullan; kimydi? yazma.",
                "yapay iyelik kalıbı": "Konu adını özne yap; konu adına iyelik eki getirip 'tarihi nedir?' kalıbı kurma.",
                "tekrarlı soru ifadesi": "Tarih ve ne zaman ifadelerinden yalnızca birini kullan.",
                "cevapta olmayan tarih sorusu": "CEVAP'ta tarih veya yıl yok; tarihle ilgili soru sorma. Yalnızca CEVAP'taki bilgiyi sor.",
                "birden fazla bilgi sorulmuş": "Tek bir bilgi hedefle; ikinci bir soruyu 've' ile ekleme.",
                "sonuç yerine neden sorulmuş": "İstenen sonuçtur; nedeni değil, verilen nedenin ardından ne olduğunu sor.",
                "neden yerine sonuç sorulmuş": "İstenen nedendir; sonucu değil, verilen sonucun hangi sebepten doğduğunu sor.",
                "cevap biçimiyle uyuşmayan soru": "Soru kalıbı cevap biçimine uysun: tarih aralığında 'hangi tarihler arasında', tek tarihte 'hangi tarihte' diye sor.",
                "yıl cevabına uygun olmayan soru": "Cevap yalnızca yıl; 'hangi yıl' diye sor.",
                "tarih cevabına uygun olmayan soru": "Cevap tam tarih; 'hangi tarihte' veya 'ne zaman' diye sor.",
                "antlaşma fiili": "Konu bir antlaşmadır; 'ne zaman imzalandı?' diye sor, 'kuruldu' deme.",
                "yazım veya çekim hatası": "Türkçedeki yazımı ve fiil çekimini düzelt; kavram adını fiile dönüştürmeden doğal bir soru kur.",
            }
            retry_hint = hints.get(
                problem,
                "Önceki soru " + problem + " nedeniyle uygun değildi. "
                "Özneyi önce getir ve dil bilgisi tamamlanmış, doğal bir doğrudan soru yaz.",
            )
            continue
        question = " ".join(question.split())
        question = ("İ" if question[0] == "i" else question[0].upper()) + question[1:]
        try:
            reply = _parse(ask(VERIFY_PROMPT.format(sentence=sentence, question=question),
                               json_schema=ANSWER_SCHEMA, temperature=0.0), "cevap")
        except Exception:
            why["model_hatasi"] = why.get("model_hatasi", 0) + 1
            return None
        if _same_answer(reply, fact["cevap"]):
            return question
        why["cevap tutmadı"] = why.get("cevap tutmadı", 0) + 1
        retry_hint = (
            "Önceki soru yalnızca kaynak cümleye göre doğru cevabı vermedi. "
            "Kaynakta açıkça bulunan bilgiyi hedefleyen daha net bir soru yaz."
        )
    return None


def _build(
    fact: dict, pool: list[str], rng: random.Random, ask, why: dict,
    exclude_questions=None,
) -> dict | None:
    if fact["kind"] not in ("tablo", "causal") and not _complete_sentence(fact):
        why["eksik cümle"] = why.get("eksik cümle", 0) + 1
        return None
    if fact["kind"] == "tablo":
        answer = fact["cevap"]
        sentence_count = len(re.split(r"(?<=[.!?])\s+", answer.strip()))
        if len(answer.split()) > 10 or sentence_count > 1 or ";" in answer:
            why["birden çok bilgi"] = why.get("birden çok bilgi", 0) + 1
            return None
    wrong = _distractors(fact, pool, rng)
    if not wrong:
        why["şık yetmedi"] = why.get("şık yetmedi", 0) + 1
        return None
    options = wrong + [fact["cevap"]]
    rng.shuffle(options)
    index = options.index(fact["cevap"])
    if _length_giveaway(options, index):
        return None
    stem = fact["govde"]
    if fact["kind"] in ("kalin", "tanim", "tablo", "causal"):
        stem = _natural_stem(fact, ask, why, exclude_questions=exclude_questions)
        if stem is None:
            return None
    return {
        "soru": stem,
        "secenekler": options,
        "dogru_index": index,
        "aciklama": f"Doğru cevap: {fact['cevap']}.",
        "sayfa": fact["page"],
        "kaynak_alinti": fact["alinti"],
    }


def _order(facts: list[dict], rng: random.Random) -> list[dict]:
    """Önce neden-sonuç ve sonuç/karar sorularını sıralar."""
    def priority(fact):
        if fact["kind"] == "causal":
            group = 0
        elif fact.get("table_category") == "outcome":
            group = 1
        elif fact.get("table_category") == "person" or fact.get("table_category") == "actor":
            group = 2
        else:
            group = 3
        return (group, -(fact["puan"] + rng.random() * 0.5))
    return sorted(facts, key=priority)


def generate_questions(
    pages, n: int = 5, seed=None, progress=None, stats: dict | None = None,
    ask=None, summary_text: str | None = None, exclude_questions=None,
):
    """Önem sırasına göre n soru üretir.

    pages: extract_pages çıktısı. Dönen her soru:
      {'soru','secenekler','dogru_index','aciklama','sayfa','kaynak_alinti'}
    stats verilirse aday sayıları ve eleme nedenleri doldurulur (tanılama için).
    """
    ask = ask or ask_llm
    why: dict = {}
    rng = random.Random(seed)
    pages = heal_pages(pages)
    facts = build_facts(pages)
    covered = " ".join(f["baglam"] for f in facts)
    facts += [f for f in build_definition_facts(pages) if f["baglam"] not in covered]
    facts += build_causal_facts(pages)
    family_map = {}
    for fact in facts:
        answer = norm(fact["cevap"])
        family = "person" if _likely_person_name(fact["cevap"]) else _answer_family(
            fact["cevap"] + " " + fact["baglam"]
        )
        if family:
            family_map[answer] = family
    for fact in facts:
        fact["family_map"] = family_map
    page_texts = {p["page"]: p.get("text", "") for p in pages}
    for fact in facts:
        if fact["kind"] != "tablo":
            continue
        item = fact.get("table_item", "")
        nearby = norm(page_texts.get(fact["page"], ""))
        name = norm(item)
        # PDF table extraction can separate an agreement's label from its row;
        # use the full page to recover the semantic type when they are adjacent.
        pos = nearby.find(name) if name else -1
        context = nearby[max(0, pos - 100):pos + len(name) + 100] if pos >= 0 else ""
        if ("franklin-bouillon" in name or "antlaşma" in norm(item)
                or "antlasma" in norm(item)
                or re.search(r"antlaşma|antlasma|anlaşma|antlasma", context)):
            fact["item_kind"] = "antlaşma"
    people = list(dict.fromkeys(
        f["cevap"] for f in facts
        if (f.get("table_category") == "person"
            or (f["kind"] == "kalin" and f["tur"] == "terim_ozel"
                and _likely_person_name(f["cevap"])))
        and _likely_person_name(f["cevap"])
    ))
    for fact in facts:
        if fact.get("table_category") == "person":
            fact["havuz"] = [name for name in people if norm(name) != norm(fact["cevap"])]
    pool = pool_from_facts(facts, pages)
    if summary_text:
        summary = norm(summary_text)

        def appears_in_summary(fact):
            # Causal facts survive when the summary paraphrases the result but
            # retains the relation's key terms.
            answer = norm(
                f"{fact.get('cause', '')} {fact.get('effect', '')} {fact['cevap']}"
                if fact["kind"] == "causal" else fact["cevap"]
            )
            if answer and answer in summary:
                return True
            tokens = [t for t in re.findall(r"[a-zçğıöşü0-9]+", answer) if len(t) >= 4]
            if not tokens:
                return False
            matches = sum(token[:5] in summary for token in tokens)
            ratio = 0.4 if fact["kind"] == "causal" else 0.6
            return matches >= max(1, math.ceil(len(tokens) * ratio))

        facts = [fact for fact in facts if appears_in_summary(fact)]
    # Dates are useful as distractors, but the user asked for understanding
    # causes/results rather than chronology recall. Do not let date facts fill
    # the quiz when relational facts are available.
    facts = [
        f for f in facts
        if not (f.get("table_category") == "date" or
                (f["kind"] != "causal" and tur(f["cevap"]) == "tarih"))
    ]
    ordered = _order(facts, rng)

    page_count = len({f["page"] for f in ordered}) or 1
    limits = [
        (math.ceil(n / page_count) + 1, max(1, math.ceil(n / 3)), math.ceil(0.6 * n)),  # dengeli
        (n, n, n),                                                                      # gevşek
    ]

    questions, used_answers, used_rows, used_person_answers = [], set(), set(), []
    used_question_texts = {norm(q) for q in (exclude_questions or [])}
    per_page, per_table = {}, {}
    rejected = 0
    for page_cap, table_cap, table_total in limits:
        for fact in ordered:
            if len(questions) >= n:
                break
            row_id = fact["satir_kimligi"]
            if row_id in used_rows or norm(fact["cevap"]) in used_answers:
                continue
            if _likely_person_name(fact["cevap"]) and any(
                _same_person_alias(fact["cevap"], prior) for prior in used_person_answers
            ):
                continue
            if per_page.get(fact["page"], 0) >= page_cap:
                continue
            tkey = row_id[:3] if fact["kind"] == "tablo" else None
            if tkey and (per_table.get(tkey, 0) >= table_cap
                         or sum(per_table.values()) >= table_total):
                continue
            relational = fact["kind"] == "causal" or fact.get("table_category") == "outcome"
            q = _build(fact, pool, rng, ask, why, exclude_questions=exclude_questions)
            if q is None:
                rejected += 1
                continue
            question_key = norm(q["soru"])
            if question_key in used_question_texts:
                rejected += 1
                continue
            used_question_texts.add(question_key)
            q["relational"] = relational
            questions.append(q)
            used_rows.add(row_id)
            used_answers.add(norm(fact["cevap"]))
            if _likely_person_name(fact["cevap"]):
                used_person_answers.append(fact["cevap"])
            per_page[fact["page"]] = per_page.get(fact["page"], 0) + 1
            if tkey:
                per_table[tkey] = per_table.get(tkey, 0) + 1
            if progress:
                progress(len(questions), n)
        if len(questions) >= n:
            break

    if stats is not None:
        stats.update(aday=len(facts), elenen=rejected, uretilen=len(questions), nedenler=why)
    # aynı sayfadaki sorular peş peşe gelmesin
    rng.shuffle(questions)
    return questions
