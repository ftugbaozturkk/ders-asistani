import hashlib
import os
import tempfile
import uuid

import streamlit as st

from src.chunker import chunk_pages
from src.pdf_loader import extract_pages
from src.question_generator import generate_questions
from src.rag import answer_question
from src.summarizer import summarize_pages
from src.vector_store import build_index

st.set_page_config(
    page_title="Yapay Zekâ Öğrenci Asistanı",
    page_icon="🎓",
    layout="wide"
)

# ---------------------------------------------------------------------------
# Oturum durumu
# ---------------------------------------------------------------------------

# İlk sohbeti oluştur
if "sohbetler" not in st.session_state:
    ilk_id = str(uuid.uuid4())

    st.session_state.sohbetler = {
        ilk_id: {
            "baslik": "Yeni Sohbet",
            "mesajlar": []
        }
    }

    st.session_state.aktif_sohbet = ilk_id

# Bu oturumda işlenip indekslenen PDF'lerin kimlikleri
if "islenen_pdfler" not in st.session_state:
    st.session_state.islenen_pdfler = set()


# ---------------------------------------------------------------------------
# Yardımcı fonksiyonlar
# ---------------------------------------------------------------------------

def yeni_sohbet():
    """Yeni, boş bir sohbet oluşturup aktif yapar."""
    sohbet_id = str(uuid.uuid4())

    st.session_state.sohbetler[sohbet_id] = {
        "baslik": "Yeni Sohbet",
        "mesajlar": []
    }

    st.session_state.aktif_sohbet = sohbet_id


def pdf_kimligi(dosya):
    """PDF içeriğinden kısa ve kararlı bir kimlik üretir."""
    return hashlib.sha256(dosya.getvalue()).hexdigest()[:16]


def pdf_sayfalari(dosya):
    """Yüklenen PDF dosyasından sayfa metinlerini çıkarır."""
    kimlik = pdf_kimligi(dosya)
    cache = st.session_state.setdefault("pdf_sayfa_cache", {})
    if kimlik in cache:
        return cache[kimlik]
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as gecici:
        gecici.write(dosya.getvalue())
        yol = gecici.name
    try:
        sayfalar = extract_pages(yol)
        # Oturum belleğini sınırlı tut; yakın zamanda kullanılan 3 PDF saklanır.
        cache[kimlik] = sayfalar
        while len(cache) > 3:
            cache.pop(next(iter(cache)))
        return sayfalar
    finally:
        os.unlink(yol)


def pdfleri_isle(dosyalar):
    """PDF'leri parçalayıp vektör veritabanına kaydeder. Toplam parça sayısını döndürür."""
    toplam = 0
    for dosya in dosyalar:
        kimlik = pdf_kimligi(dosya)
        parcalar = chunk_pages(pdf_sayfalari(dosya))

        # Parça kimliklerine PDF kimliği eklenir: iki PDF birbirinin üzerine yazmaz.
        for parca in parcalar:
            parca["id"] = f"{kimlik}_{parca['id']}"

        if parcalar:
            build_index(parcalar, source_id=kimlik)
            st.session_state.islenen_pdfler.add(kimlik)
            toplam += len(parcalar)
    return toplam


def pdfler_hazir(dosyalar):
    """Yüklü PDF'lerin hepsi bu oturumda işlendi mi?"""
    return bool(dosyalar) and all(
        pdf_kimligi(d) in st.session_state.islenen_pdfler for d in dosyalar
    )


def ozet_olustur(dosya, sohbet_id):
    """PDF'in özetini çıkarıp sohbete asistan mesajı olarak ekler."""
    kimlik = pdf_kimligi(dosya)
    cache = st.session_state.setdefault("pdf_ozet_cache", {})
    ozet = cache.get(kimlik)
    if not ozet:
        durum = st.empty()
        durum.info("PDF sayfaları okunuyor...")
        ilerleme = st.progress(0, text="Özet hazırlanıyor...")
        try:
            sayfalar = pdf_sayfalari(dosya)
            ozet = summarize_pages(
                sayfalar,
                progress=lambda i, n: ilerleme.progress(
                    i / n, text=f"Özet hazırlanıyor... bölüm {i}/{n} işleniyor"
                ),
            )
        finally:
            ilerleme.empty()
            durum.empty()
        cache[kimlik] = ozet
    messages = st.session_state.sohbetler[sohbet_id]["mesajlar"]
    title = f"📝 **{dosya.name} özeti**"
    existing = next((
        message for message in messages
        if message["rol"] == "assistant" and message["icerik"].startswith(title)
    ), None)
    summary_message = {
        "rol": "assistant",
        "icerik": (
            f"{title}\n\n{ozet}\n\n"
            "*Bu özet yapay zekâ tarafından üretildi. Önemli "
            "bilgileri belirtilen sayfalardan kontrol et.*"
        ),
        "kaynaklar": [],
    }
    if existing:
        existing.update(summary_message)
    else:
        messages.append(summary_message)


def sorulari_uret(dosya, adet):
    """Özetteki bilgilerden, daha önce sorulmayan quiz üretir."""
    sayfalar = pdf_sayfalari(dosya)
    kimlik = pdf_kimligi(dosya)
    ozet_cache = st.session_state.setdefault("pdf_ozet_cache", {})
    ozet = ozet_cache.get(kimlik)
    if not ozet:
        raise ValueError("Önce 'PDF Özeti' düğmesiyle özeti oluşturup görüntüle.")

    history = st.session_state.setdefault("quiz_soru_gecmis", {})
    asked_questions = history.setdefault(kimlik, [])
    ilerleme = st.progress(
        0, text="Sorular hazırlanıyor... (birkaç dakika sürebilir)"
    )
    try:
        sorular = generate_questions(
            sayfalar,
            n=adet,
            summary_text=ozet,
            exclude_questions=asked_questions,
            progress=lambda i, n: ilerleme.progress(
                i / n, text=f"Sorular hazırlanıyor... {i}/{n}"
            ),
        )
    finally:
        ilerleme.empty()
    if sorular:
        history[kimlik] = list(dict.fromkeys(
            asked_questions + [s["soru"] for s in sorular]
        ))[-300:]
    return sorular


# ---------------------------------------------------------------------------
# Quiz
# ---------------------------------------------------------------------------

def quiz_temizle():
    """Quiz'i ve seçilen şıkları sıfırlar."""
    for anahtar in [k for k in st.session_state.keys() if k.startswith("quiz_secim_")]:
        del st.session_state[anahtar]
    st.session_state.pop("quiz", None)
    st.session_state.pop("quiz_ozet_oncesi", None)


def _quiz_kaydet(i):
    """Bir şık seçilince cevabı kaydeder (soru kilitlenir)."""
    st.session_state.quiz["cevaplar"][i] = st.session_state[f"quiz_secim_{i}"]


def quiz_goster():
    quiz = st.session_state.quiz
    sorular = quiz["sorular"]
    cevaplar = quiz["cevaplar"]

    if st.button("⬅️ Sohbete dön"):
        quiz_temizle()
        st.rerun()

    st.header(f"📝 Quiz: {quiz['pdf_adi']}")
    st.caption(
        "Sorular yapay zekâ tarafından üretildi. Şüphe ettiğin cevabı "
            "belirtilen sayfadan kontrol et. Quiz ağırlıklı olarak neden-sonuç "
            "ve sonuç ilişkilerini ölçer."
    )
    if len(sorular) < quiz.get("istenen", len(sorular)):
        neden_sonuc = sum(1 for soru in sorular if soru.get("relational"))
        st.info(
            f"İstenen {quiz.get('istenen', len(sorular))} sorudan {len(sorular)} soru üretildi; "
            f"{neden_sonuc} tanesi neden-sonuç/sonuç ilişkisi sorusu. "
            "Özette yeterli yeni bilgi veya güvenilir çeldirici bulunmayan sorular eklenmedi."
        )

    dogru = sum(
        1
        for i, secim in cevaplar.items()
        if secim == sorular[i]["secenekler"][sorular[i]["dogru_index"]]
    )
    st.progress(
        len(cevaplar) / len(sorular),
        text=f"{len(cevaplar)}/{len(sorular)} cevaplandı · {dogru} doğru",
    )
    if len(cevaplar) == len(sorular):
        st.success(f"🎉 Quiz bitti! Sonuç: {dogru} / {len(sorular)}")

    for i, s in enumerate(sorular):
        with st.container(border=True):
            st.markdown(f"**{i + 1}. {s['soru']}**")
            st.radio(
                "Cevabın",
                s["secenekler"],
                index=None,
                key=f"quiz_secim_{i}",
                disabled=i in cevaplar,  # cevaplanınca kilitlenir
                on_change=_quiz_kaydet,
                args=(i,),
                label_visibility="collapsed",
            )
            if i in cevaplar:
                dogru_secenek = s["secenekler"][s["dogru_index"]]
                if cevaplar[i] == dogru_secenek:
                    st.success("✅ Doğru!")
                else:
                    st.error(
                        f"❌ Yanlış. Senin cevabın: {cevaplar[i]}  \n"
                        f"Doğru cevap: **{dogru_secenek}**"
                    )
                if s["aciklama"]:
                    st.write("💡 " + s["aciklama"])
                st.text(f"📌 Sayfa {s['sayfa']}: {s['kaynak_alinti']}")


# ---------------------------------------------------------------------------
# Sol panel
# ---------------------------------------------------------------------------

with st.sidebar:

    st.title("🎓 Öğrenci Paneli")

    if st.button("➕ Yeni Sohbet", use_container_width=True):
        yeni_sohbet()
        st.rerun()

    st.divider()

    st.subheader("💬 Sohbetlerim")

    for sohbet_id in reversed(list(st.session_state.sohbetler.keys())):

        sohbet = st.session_state.sohbetler[sohbet_id]

        if st.button(
            "💬 " + sohbet["baslik"],
            key="sohbet_" + sohbet_id,
            use_container_width=True
        ):
            st.session_state.aktif_sohbet = sohbet_id
            st.rerun()

    st.divider()

    # PDF yükleme
    st.subheader("📄 PDF Dosyalarım")

    aktif_id = st.session_state.aktif_sohbet

    dosyalar = st.file_uploader(
        "Ders notlarını buraya yükle",
        type=["pdf"],
        accept_multiple_files=True,
        key="pdf_" + aktif_id
    )

    if dosyalar:
        st.success(f"{len(dosyalar)} PDF yüklendi!")

        for dosya in dosyalar:
            st.write("📄 " + dosya.name)

        if st.button("📚 PDF'leri İşle", key="pdf_isle"):
            try:
                with st.spinner("PDF dosyaları işleniyor..."):
                    toplam_parca = pdfleri_isle(dosyalar)

                st.success(
                    f"PDF'ler işlendi! "
                    f"{toplam_parca} metin parçası hazır."
                )

            except Exception as hata:
                st.error(f"PDF işleme hatası: {hata}")

        secilen_pdf = st.selectbox(
            "Önizlenecek PDF:",
            range(len(dosyalar)),
            format_func=lambda i: dosyalar[i].name
        )

        if st.checkbox("📖 PDF Önizleme"):
            import pymupdf

            with pymupdf.open(
                stream=dosyalar[secilen_pdf].getvalue(), filetype="pdf"
            ) as belge:
                toplam = len(belge)
                sayfa_no = st.number_input(
                    "Sayfa",
                    min_value=1,
                    max_value=toplam,
                    value=1,
                    step=1,
                    key=f"onizleme_sayfa_{secilen_pdf}",
                )
                resim = belge[sayfa_no - 1].get_pixmap(dpi=110).tobytes("png")

            st.image(resim, caption=f"Sayfa {sayfa_no} / {toplam}")

    else:
        st.info("Henüz PDF yüklenmedi.")

    st.divider()

    # Çalışma araçları
    st.subheader("🧠 Çalışma Araçları")

    if st.button("📝 PDF Özeti", use_container_width=True):
        if not dosyalar:
            st.warning("Önce bir PDF yükle.")
        else:
            try:
                # Quiz ekranda kalırsa sohbet mesajı gizli olur. Özeti gösterirken
                # quiz'i sakla; kullanıcı tek tıkla aynı quiz'e dönebilir.
                if st.session_state.get("quiz"):
                    st.session_state.quiz_ozet_oncesi = st.session_state.pop("quiz")
                ozet_olustur(dosyalar[secilen_pdf], aktif_id)
            except Exception as hata:
                st.error(f"Özet hatası: {hata}")
            else:
                st.rerun()

    soru_sayisi = st.slider("Soru sayısı", 5, 15, 5)

    if st.button("✅ Soru Oluştur", use_container_width=True):
        if not dosyalar:
            st.warning("Önce bir PDF yükle.")
        elif pdf_kimligi(dosyalar[secilen_pdf]) not in st.session_state.get("pdf_ozet_cache", {}):
            st.warning("Önce 'PDF Özeti' düğmesiyle özeti oluşturup görüntüle.")
        else:
            secilen = dosyalar[secilen_pdf]
            try:
                sorular = sorulari_uret(secilen, soru_sayisi)
            except Exception as hata:
                st.error(f"Soru üretme hatası: {hata}")
            else:
                if not sorular:
                    st.warning(
                        "Özette kalan, daha önce sorulmamış ve tek doğru cevabı olan "
                        "yeterli soru bulunamadı. Başka bir PDF ekleyebilirsin."
                    )
                else:
                    quiz_temizle()
                    st.session_state.quiz = {
                        "pdf_adi": secilen.name,
                        "sorular": sorular,
                        "cevaplar": {},
                        "istenen": soru_sayisi,
                    }
                    st.rerun()


# ---------------------------------------------------------------------------
# Ana sohbet ekranı
# ---------------------------------------------------------------------------

aktif_id = st.session_state.aktif_sohbet

aktif_sohbet = st.session_state.sohbetler[aktif_id]

st.title("🎓 Yapay Zekâ Öğrenci Asistanı")

st.caption(
    "PDF dosyalarını yükle, sorularını sor, derslerine hazırlan!"
)

if pdfler_hazir(dosyalar):
    st.success(
        f"📚 {len(dosyalar)} PDF seçili. Sorularını sorabilirsin."
    )
elif dosyalar:
    st.warning(
        "PDF'ler henüz işlenmedi. Sorulardan önce sol panelden "
        "'📚 PDF'leri İşle' düğmesine bas."
    )
else:
    st.info("📄 Başlamak için sol panelden PDF yükleyebilirsin.")

st.divider()

# Quiz açıksa sohbet yerine quiz'i göster
if st.session_state.get("quiz"):
    quiz_goster()
    st.stop()

if st.session_state.get("quiz_ozet_oncesi"):
    if st.button("📝 Özeti gördüm — quiz'e geri dön"):
        st.session_state.quiz = st.session_state.pop("quiz_ozet_oncesi")
        st.rerun()

# Sohbet mesajlarını göster
for mesaj in aktif_sohbet["mesajlar"]:

    with st.chat_message(mesaj["rol"]):
        st.write(mesaj["icerik"])

        if mesaj.get("kaynaklar"):
            with st.expander("📌 Dayandığı kaynaklar"):
                for k in mesaj["kaynaklar"]:
                    st.markdown(
                        f"**Sayfa {k['page']}** "
                        f"(uzaklık {k['distance']:.3f})"
                    )
                    st.text(k["text"][:300])


# Mesaj yazma alanı
soru = st.chat_input("PDF hakkında bir soru sor...")

if soru:

    # İlk sorudan sohbet başlığı oluştur
    if not aktif_sohbet["mesajlar"]:
        aktif_sohbet["baslik"] = (
            soru[:30] + "..."
            if len(soru) > 30
            else soru
        )

    aktif_sohbet["mesajlar"].append({
        "rol": "user",
        "icerik": soru
    })

    # Gerçek RAG sistemiyle cevap oluştur
    kaynaklar = []

    if not dosyalar:
        cevap = "Önce bir PDF yüklemelisin."

    elif not pdfler_hazir(dosyalar):
        cevap = "Önce sol panelden '📚 PDF'leri İşle' düğmesine basmalısın."

    else:
        try:
            with st.spinner("Yapay zekâ cevap hazırlıyor..."):
                kaynak_kimlikleri = [pdf_kimligi(d) for d in dosyalar]
                sonuc = answer_question(soru, source_ids=kaynak_kimlikleri)
                cevap = sonuc["answer"]
                if sonuc["found"]:
                    kaynaklar = sonuc["sources"]

        except Exception as hata:
            cevap = f"Yapay zekâ hatası: {hata}"

    aktif_sohbet["mesajlar"].append({
        "rol": "assistant",
        "icerik": cevap,
        "kaynaklar": kaynaklar
    })

    st.rerun() 
