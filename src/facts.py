"""PDF'in yapısından (kalın yazı, tablo) 'önemli bilgi' adaylarını çıkarır.

Model kullanmaz. Önemli kabul edilenler:
  - Yazarın kalın yazdığı terim/tarih/ad (vurgulamış demektir),
  - Tablo hücreleri (yazar bilgiyi tabloya koymuşsa kıyaslanabilir bir bilgidir),
  - Belgede tekrar tekrar geçen kavramlar (yalnızca yedek yöntem).
Hiçbir kelime listesi belirli bir derse özel değildir.
"""
import re
from collections import Counter

BLANK = "_____"
BULLET_RE = re.compile(r"^[\s•·▪●○■□►\-–—*]+")
SYMBOL_RE = re.compile(r"[≠=→≈↔⇒]")
# boşluk bir sıralamanın (virgüllü liste / parantez) içindeyse soru anlamsız olur
LIST_BLANK_RE = re.compile(r"[,;(]\s*_____\s*[,;)]|\(\s*_____|_____\s*\)")
# "_____ Büyük Harfle Başlayan Cümle" = etiket kalın yazılmış, noktalama yok
LABEL_START_RE = re.compile(r"^_____\s+[A-ZÇĞİÖŞÜ]")

MONTHS = ("ocak|şubat|mart|nisan|mayıs|haziran|temmuz|ağustos|eylül|ekim|kasım|aralık")
MONTHS_CAP = "Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık"
DATE_RE = re.compile(rf"\b\d{{3,4}}\b|\b({MONTHS})\b")
DIGIT_RE = re.compile(r"\d")
DATE_LEAD_RE = re.compile(rf"^(?P<d>(?:\d{{1,2}} )?(?:{MONTHS_CAP}) \d{{4}}|\d{{4}})\s*[:–-]\s+(?P<rest>.+)$")
# "Bu PDF'i nasıl çalışmalısın" türü üst bilgi cümleleri
META_RE = re.compile(r"(pdf|sayfay|bu sayfa|çalışma notu|nasıl çalış|not defter|sınav öncesi|içindekiler|kalın yaz)")
ENUM_RE = re.compile(r"^\s*\(?[A-Ea-e1-9]\s*[\)\.]\s", re.M)
STOP = set("""ancak çünkü olarak ayrıca bunun bunlar kadar sonra önce fakat veya ile için gibi
daha çok bile diğer başka böylece yani ise hem ama ancak şöyle böyle burada orada olan olur
oldu olmuş olması olduğu edilen edildi yapılan yapıldı bulunan bunu şunu onun onlar bütün her
bazı birçok tüm göre karşı rağmen sırasında ardından sonucunda""".split())
PERSON_COLUMNS = ("başkan", "komutan", "kişi", "temsilci", "lider")
CAUSAL_COLUMNS = ("sonuç", "neden", "sebep", "amaç", "etki", "anlam", "karar", "önem")
DETAIL_COLUMNS = CAUSAL_COLUMNS + ("not",)
DATE_COLUMNS = ("tarih", "yıl", "kronoloji")
CAUSE_TO_EFFECT_RE = re.compile(
    r"(?P<cause>[^.;:\n]{8,120}?)\s+(?:çıkınca|olunca|bastırılınca|çağırınca|yaklaşınca|"
    r"sonucunda|bu nedenle|bu yüzden|dolayısıyla|bu sebeple)\s+"
    r"(?P<effect>[^.;:\n]{8,120})",
    re.I,
)
EFFECT_TO_CAUSE_RE = re.compile(
    r"(?P<left>[^.;:\n]{8,140}?)\s+(?:yüzünden|nedeniyle)\s+(?P<effect>[^.;:\n]{5,100})",
    re.I,
)


def norm(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower()


PURE_DATE_RE = re.compile(rf"^[\d\s\-–—./]*(({MONTHS})[\d\s\-–—./]*)*$")


def tur(text: str) -> str:
    """tarih (yalnızca tarih), terim_tarih (ad + tarih), sayi, terim."""
    t = norm(text).strip()
    if DATE_RE.search(t):
        return "tarih" if PURE_DATE_RE.match(t) else "terim_tarih"
    if DIGIT_RE.search(t):
        return "sayi"
    words = re.findall(r"\S+", text.strip())
    if words and all(w[0].isupper() for w in words if w[0].isalpha()):
        return "terim_ozel"  # özel ad gibi (her sözcük büyük harfle başlıyor)
    return "terim"


def _kalin_temizle(run: str) -> str:
    return run.strip(" :;,.-–—")


def _sentence_with_blank(text: str) -> str:
    """Uzun bloklarda yalnızca boşluğun bulunduğu cümleyi döndürür."""
    if len(text) <= 320:
        return text
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÇĞİÖŞÜ0-9«\"(])", text)
    for part in parts:
        if BLANK in part:
            return part
    return text


def _is_question_list(text: str) -> bool:
    return len(ENUM_RE.findall(text)) >= 6 or text.count("?") >= 5


def _repeated_blocks(pages: list[dict]) -> set[str]:
    """Çoğu sayfada tekrar eden blokları (üst bilgi / alt bilgi) bulur."""
    seen = Counter()
    for p in pages:
        keys = {re.sub(r"\d+", "#", norm(b["metin"])) for b in p["yapi"]["bloklar"]}
        seen.update(keys)
    limit = max(2, int(0.4 * len(pages)))
    return {k for k, c in seen.items() if c >= limit}


def _corpus(pages: list[dict]) -> str:
    return norm(" ".join(p["text"] for p in pages))


def _healer(pages: list[dict]):
    """PDF'in bozduğu bölünmüş sözcükleri ("Eski şehir") belgenin başka yerindeki
    bütün haline ("Eskişehir") bakarak birleştirir."""
    tokens = Counter(re.findall(r"[^\W\d_]+", norm(" ".join(p["text"] for p in pages))))
    text_all = norm(" ".join(p["text"] for p in pages))

    def heal(text: str) -> str:
        parts = text.split(" ")
        out = []
        for part in parts:
            if out:
                a, b = out[-1], part
                ja = re.sub(r"[^\w]", "", norm(a))
                jb = re.sub(r"[^\w]", "", norm(b))
                if ja and jb and a[-1].isalpha() and b[0].isalpha() and tokens[ja + jb] >= 1 \
                        and text_all.count(ja + " " + jb) <= tokens[ja + jb]:
                    out[-1] = a + b
                    continue
            out.append(part)
        return " ".join(out)

    return heal


def heal_pages(pages: list[dict]) -> list[dict]:
    """Yapı içindeki metinleri onarılmış kopyasıyla döndürür (orijinali değiştirmez)."""
    heal = _healer([p for p in pages])
    fixed = []
    for p in pages:
        if "yapi" not in p:
            fixed.append(p)
            continue
        y = p["yapi"]
        fixed.append({**p, "yapi": {
            "bloklar": [{**b, "metin": heal(b["metin"]), "kalin": [heal(r) for r in b["kalin"]]}
                        for b in y["bloklar"]],
            "tablolar": [{"baslik": [heal(c) for c in t["baslik"]],
                          "satirlar": [[heal(c) for c in r] for r in t["satirlar"]]}
                         for t in y["tablolar"]],
        }})
    return fixed


def build_facts(pages: list[dict]) -> list[dict]:
    """Soru adaylarını döndürür.

    Her aday: kind, page, puan, cevap, tur, govde (soru gövdesi), baglam, alinti,
    havuz_anahtari (şık havuzu), satir_kimligi.
    """
    pages = [p for p in pages if "yapi" in p and not _is_question_list(p["text"])]
    corpus = _corpus(pages)
    repeated = _repeated_blocks(pages)
    facts = []
    # Her yerde tekrar eden tek sözcüklük kalın etiketler ("Sonuç", "Ezber" gibi) soru konusu değildir
    first_words = Counter(
        m.group(1) for p in pages for b in p["yapi"]["bloklar"]
        if (m := re.match(r"^[•\s]*([^\W\d_]{3,}) [A-ZÇĞİÖŞÜ]", b["metin"]))
    )
    label_count = Counter(
        norm(_kalin_temizle(r)) for p in pages for b in p["yapi"]["bloklar"]
        for r in b["kalin"] if len(r.split()) == 1
    )

    for p in pages:
        # ---------------- tarih ile başlayan maddeler ----------------
        for bi, b in enumerate(p["yapi"]["bloklar"]):
            text = BULLET_RE.sub("", b["metin"])
            m = DATE_LEAD_RE.match(text)
            if b["tamami_kalin"] or not m or META_RE.search(norm(text)) \
                    or re.sub(r"\d+", "#", norm(b["metin"])) in repeated:
                continue
            date, rest = m["d"].strip(), m["rest"].strip()
            if len(rest) < 25 or SYMBOL_RE.search(rest):
                continue
            facts.append({
                "kind": "kalin", "page": p["page"], "puan": 1.5, "cevap": date, "tur": "tarih",
                "govde": f"{BLANK}: {rest}", "baglam": text,
                "alinti": text if len(text) <= 300 else text[:300] + "…",
                "satir_kimligi": ("dl", p["page"], bi),
            })

        # ---------------- kalın yazılar ----------------
        for bi, b in enumerate(p["yapi"]["bloklar"]):
            if b["tamami_kalin"] or not b["kalin"]:
                continue
            if re.sub(r"\d+", "#", norm(b["metin"])) in repeated:
                continue
            if META_RE.search(norm(b["metin"])):
                continue
            text = BULLET_RE.sub("", b["metin"])
            kalin = list(b["kalin"])
            first = _kalin_temizle(kalin[0]) if kalin else ""
            if first and len(first.split()) == 1 and label_count[norm(first)] >= 3 \
                    and text.startswith(kalin[0]):
                text = text[len(kalin[0]):].lstrip(" :-–—")  # "Sonuç ..." gibi etiketi at
                kalin = kalin[1:]
            lead = re.match(r"^([^\W\d_]{3,}) [A-ZÇĞİÖŞÜ]", text)
            if lead and first_words[lead.group(1)] >= 3:  # etiket: "Sonuç İsyanlar..."
                text = text[len(lead.group(1)):].lstrip()
                kalin = [r for r in kalin if r != lead.group(1)]
            runs = []
            for r in kalin:  # yan yana duran kalın parçaları birleştir
                if runs and (runs[-1] + " " + r in text or runs[-1] + r in text):
                    runs[-1] = runs[-1] + (" " if runs[-1] + " " + r in text else "") + r
                else:
                    runs.append(r)
            for run in runs:
                answer = _kalin_temizle(run)
                words = len(answer.split())
                if not (2 <= len(answer) <= 60 and words <= 7) or "?" in answer:
                    continue
                if not re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]{2}", answer) and not re.search(r"\d{3,}", answer):
                    continue
                if answer.startswith(("(", ")")):
                    continue
                if ":" in answer or (re.search(r"\d", answer) and not answer.endswith(")")
                                     and tur(answer) != "tarih"):
                    continue  # "28 Ocak 1920: Olay" gibi satır başlığı ya da cümle parçası
                if words == 1 and (len(answer) < 6 or label_count[norm(answer)] >= 3):
                    continue
                if text.count(run) != 1 and text.count(answer) != 1:
                    continue
                stem = text.replace(run, BLANK, 1) if run in text else text.replace(answer, BLANK, 1)
                stem = _sentence_with_blank(stem)
                rest = stem.replace(BLANK, "").strip()
                if len(rest) < 30 or len(answer) > 0.6 * len(stem):
                    continue
                if SYMBOL_RE.search(stem) or LIST_BLANK_RE.search(stem) or LABEL_START_RE.match(stem):
                    continue
                kind_t = tur(answer)
                score = 1.7 if kind_t in ("tarih", "terim_tarih") else 3.0
                repeats = corpus.count(norm(answer)) - 1
                score += min(2.0, 0.5 * max(0, repeats))
                facts.append({
                    "kind": "kalin", "page": p["page"], "puan": score,
                    "cevap": answer, "tur": kind_t, "govde": stem, "baglam": text,
                    "alinti": text if len(text) <= 300 else text[:300] + "…",
                    "satir_kimligi": ("b", p["page"], bi),
                })

        # ---------------- tablolar ----------------
        for ti, t in enumerate(p["yapi"]["tablolar"]):
            head, rows = t["baslik"], t["satirlar"]
            if len(rows) < 3 or len(head) < 2:
                continue
            # Transposed tables have row labels in their first column and
            # entities in their header (e.g. congress -> chair/decision).

            entries = []
            if not head[0].strip():
                for ri, row in enumerate(rows):
                    attribute = row[0].strip() if row else ""
                    if not attribute:
                        continue
                    header = norm(attribute)
                    is_detail = any(word in header for word in DETAIL_COLUMNS)
                    max_chars, max_words = (220, 30) if is_detail else (60, 8)
                    values = [row[j].strip() for j in range(1, len(head))
                              if j < len(row) and row[j].strip()]
                    short = [v for v in values if len(v) <= max_chars and len(v.split()) <= max_words]
                    if len(short) < 0.7 * (len(head) - 1):
                        continue
                    for j in range(1, len(head)):
                        cell = row[j].strip() if j < len(row) else ""
                        if cell and len(cell) <= max_chars and len(cell.split()) <= max_words:
                            entries.append((head[j], attribute, cell, values,
                                            ("t", p["page"], ti, ri, j)))
            else:
                keys = [norm(r[0]) for r in rows]
                for j in range(1, len(head)):
                    if not head[j].strip():
                        continue
                    header = norm(head[j])
                    is_detail = any(word in header for word in DETAIL_COLUMNS)
                    max_chars, max_words = (220, 30) if is_detail else (60, 8)
                    values = [r[j].strip() for r in rows if j < len(r) and r[j].strip()]
                    short = [v for v in values if len(v) <= max_chars and len(v.split()) <= max_words]
                    if len(short) < 0.7 * len(rows):
                        continue
                    for ri, row in enumerate(rows):
                        item = row[0].strip() if row else ""
                        cell = row[j].strip() if j < len(row) else ""
                        if (not item or keys.count(norm(item)) != 1 or not cell
                                or len(cell) > max_chars or len(cell.split()) > max_words):
                            continue
                        entries.append((item, head[j], cell, values,
                                        ("t", p["page"], ti, ri, j)))

            for item, attribute, cell, peers, row_id in entries:
                header = norm(attribute)
                is_person = any(word in header for word in PERSON_COLUMNS)
                answer = re.sub(r"\s*\([^)]*\)", "", cell).strip() if is_person else cell
                option_peers = [
                    re.sub(r"\s*\([^)]*\)", "", value).strip() if is_person else value
                    for value in peers
                ]
                kind_t = tur(answer)
                is_causal = any(word in header for word in CAUSAL_COLUMNS)
                item_is_date = bool(re.fullmatch(r"\d{3,4}(?:\s*[–—-]\s*\d{1,4})?", item.strip()))
                if (any(word in header for word in DATE_COLUMNS)
                        or (item_is_date and any(word in header for word in ("olay", "gelişme", "konu")))):
                    score = 1.5
                    category = "date"
                elif any(word in header for word in PERSON_COLUMNS):
                    score = 5.8
                    category = "person"
                elif "taraf" in header:
                    score = 4.2
                    category = "actor"
                elif is_causal:
                    score = 5.4
                    category = "outcome"
                else:
                    score = 3.6
                    category = "other"
                facts.append({
                    "kind": "tablo", "page": p["page"],
                    "puan": score - (0.4 if len(cell) > 100 else 0.0),
                    "cevap": answer, "tur": kind_t,
                    "govde": f"{item} {attribute}: {cell}",
                    "table_item": item, "table_column": attribute,
                    "table_category": category,
                    "baglam": f"{item} | {attribute}: {cell}",
                    "alinti": f"{item} | {attribute}: {cell}"[:300],
                    "havuz": [v for v in option_peers if norm(v) != norm(answer)],
                    "satir_kimligi": row_id,
                })
    return facts


PREDICATE_RE = re.compile(r"\b\w*(?<!m[ıiuü]ş)(?:d[ıiuü]r|t[ıiuü]r)\b|\bdenir\b|\badlandırılır\b|\btanımlanır\b")
LEAD_RE = re.compile(r"^(?P<s>[A-ZÇĞİÖŞÜ][^,:;.]{3,40}?)\s*[,:]\s+(?P<rest>.+)$")


def build_definition_facts(pages: list[dict]) -> list[dict]:
    """Kalın yazı/tablo olmayan düz metinler için: tanım cümleleri.

    'X, ...dır.' / 'X ... denir.' biçimindeki cümlenin öznesi sorulur. Tanım cümleleri
    notun temel kavramlarını anlatır; kalın yazı yoksa en güvenilir önem işaretidir.
    """
    pages = [p for p in pages if "yapi" in p and not _is_question_list(p["text"])]
    repeated = _repeated_blocks(pages)
    corpus = _corpus(pages)
    facts = []
    for p in pages:
        for bi, b in enumerate(p["yapi"]["bloklar"]):
            if b["tamami_kalin"] or re.sub(r"\d+", "#", norm(b["metin"])) in repeated:
                continue
            for si, sent in enumerate(re.split(r"(?<=[.!?])\s+", b["metin"])):
                sent = sent.strip()
                if not (45 <= len(sent) <= 260) or META_RE.search(norm(sent)):
                    continue
                if not PREDICATE_RE.search(sent) or DIGIT_RE.search(sent):
                    continue
                m = LEAD_RE.match(sent)
                if not m or len(m["s"].split()) > 4 or norm(m["s"]) in STOP \
                        or "'" in m["s"] or "’" in m["s"] or len(m["rest"]) < 25:
                    continue
                subject = m["s"].strip()
                score = 2.5 + min(1.5, 0.3 * max(0, corpus.count(norm(subject)[:6]) - 1))
                facts.append({
                    "kind": "tanim", "page": p["page"], "puan": score, "cevap": subject,
                    "tur": "tanim", "govde": sent.replace(subject, BLANK, 1), "baglam": sent,
                    "alinti": sent, "satir_kimligi": ("d", p["page"], bi, si),
                })
    answers = [f["cevap"] for f in facts]
    for f in facts:
        f["havuz"] = [a for a in answers if norm(a)[:5] != norm(f["cevap"])[:5]]
    return facts


def build_causal_facts(pages: list[dict]) -> list[dict]:
    """Açık neden-sonuç bağlaçlarından sonuç veya neden soru adayları çıkarır."""
    facts, seen = [], set()

    def add(page, source, cause, effect, target, identity):
        cause = BULLET_RE.sub("", cause).strip(" \t\r\n:;,.-–—")
        effect = re.sub(r"\s*\([^)]*\)", "", effect).strip(" \t\r\n:;,.-–—")
        answer = effect if target == "sonuç" else cause
        if not (2 <= len(cause.split()) <= 16 and 2 <= len(answer.split()) <= 10):
            return
        if len(cause) < 10 or len(answer) < 8:
            return
        key = (page, norm(cause), norm(answer), target)
        if key in seen:
            return
        seen.add(key)
        facts.append({
            "kind": "causal", "page": page, "puan": 6.5,
            "cevap": answer, "tur": tur(answer),
            "cause": cause, "effect": effect, "causal_target": target,
            "govde": (f"{cause} sonucunda {BLANK}." if target == "sonuç"
                      else f"{effect} nedeniyle {BLANK}."),
            "baglam": f"Neden: {cause} | Sonuç: {effect}",
            "alinti": source[:300], "satir_kimligi": ("c", page, identity),
        })

    for page in pages:
        if "yapi" not in page:
            continue
        for ti, table in enumerate(page["yapi"]["tablolar"]):
            head, rows = table["baslik"], table["satirlar"]
            headers = [norm(cell) for cell in head]
            cause_indices = [
                i for i, header in enumerate(headers)
                if any(label in header for label in ("neden", "sebep", "gerekçe", "gerekce", "amaç", "amac"))
            ]
            effect_indices = [
                i for i, header in enumerate(headers)
                if any(label in header for label in ("sonuç", "sonuc", "etki", "karar"))
            ]
            for ri, row in enumerate(rows):
                for ci in cause_indices:
                    for ei in effect_indices:
                        cause = row[ci].strip() if ci < len(row) else ""
                        effect = row[ei].strip() if ei < len(row) else ""
                        add(page["page"], " | ".join(row), cause, effect, "sonuç", ("table", ti, ri, ci, ei))

        sources = [b["metin"] for b in page["yapi"]["bloklar"]]
        sources.extend(
            cell
            for table in page["yapi"]["tablolar"]
            for row in [table["baslik"], *table["satirlar"]]
            for cell in row
            if cell
        )
        for si, source in enumerate(sources):
            if re.search(r"\b(?:sıra|sırası|kronoloji|kronolojik)\b", norm(source)):
                continue  # chronology/sequence is not a cause-and-effect question
            for sentence in re.split(r"(?<=[.!?])\s+", source):
                if "→" not in sentence:
                    continue
                pieces = [piece.strip() for piece in sentence.split("→")]
                chain_options = [
                    re.sub(
                        r"^(?:kadın hakları|sonuç|ezber)\s*:\s*", "",
                        BULLET_RE.sub("", re.sub(r"\s*\([^)]*\)", "", piece)).strip(),
                        flags=re.I,
                    ).strip(" \t\r\n:;,.-–—")
                    for piece in pieces
                ]
                for pi, (cause, effect) in enumerate(zip(pieces, pieces[1:])):
                    before = len(facts)
                    add(page["page"], source, cause, effect, "sonuç", (si, pi))
                    if len(facts) > before:
                        facts[-1]["chain_options"] = chain_options
            for mi, match in enumerate(CAUSE_TO_EFFECT_RE.finditer(source)):
                add(page["page"], source, match["cause"], match["effect"], "sonuç", (si, "ce", mi))
            for mi, match in enumerate(EFFECT_TO_CAUSE_RE.finditer(source)):
                # Virgülden sonraki son parça genellikle doğrudan nedendir:
                # "Enver Paşa'nın planı, aşırı soğuk yüzünden büyük kayıp".
                cause = match["left"].rsplit(",", 1)[-1]
                add(page["page"], source, cause, match["effect"], "neden", (si, "ec", mi))
    for fact in facts:
        same_relation = [
            other["cevap"] for other in facts
            if other["causal_target"] == fact["causal_target"]
            and norm(other["cevap"]) != norm(fact["cevap"])
        ]
        # Prefer alternatives from the same page, then use the document's other
        # cause/effect results if a single page does not supply three choices.
        local = [
            other["cevap"] for other in facts
            if other["page"] == fact["page"]
            and other["causal_target"] == fact["causal_target"]
            and norm(other["cevap"]) != norm(fact["cevap"])
        ]
        fact["havuz"] = fact.get("chain_options") or local + same_relation
    return facts


FULL_DATE_RE = re.compile(rf"\b\d{{1,2}} (?:{MONTHS.replace('ş','[şŞ]').replace('ı','[ıI]')}|Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık) \d{{4}}\b")


def pool_from_facts(facts: list[dict], pages: list[dict] | None = None) -> list[str]:
    """Şık havuzu: kalın vurgular, tablo değerleri ve belgede geçen tam tarihler."""
    pool = [f["cevap"] for f in facts if f["kind"] in ("kalin", "tablo", "causal")]
    if pages:
        for p in pages:
            pool += FULL_DATE_RE.findall(p["text"])
    return pool
