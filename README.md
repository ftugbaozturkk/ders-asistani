# Ders Asistanı (RAG)

Ders PDF'lerinden soru yanıtlayan, tamamen yerel ve ücretsiz çalışan RAG prototipi.

## Gereksinimler
- Python 3.11
- [Ollama](https://ollama.com/download) (uygulama arka planda açık olmalı)

## Kurulum
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
ollama pull qwen2.5:7b
ollama pull bge-m3
```
İlk model indirmeleri toplam yaklaşık 6 GB, sadece bir kez internet gerekir.

## Kullanım
### Streamlit arayüzü
```powershell
streamlit run app.py
```
Arayüzden PDF yükle, **PDF'leri İşle** düğmesiyle vektör indeksini oluştur,
ardından sohbet, özet ve quiz araçlarını kullan. Sohbet araması yalnızca o an
seçili ve işlenmiş PDF'lerin vektör parçalarını kullanır.

### Komut satırı
Önce PDF'leri `data/pdfs/` klasörüne koyup indeksle:
```powershell
python index.py
```
Ardından soru-cevap arayüzünü başlat:
```powershell
python ask.py
```

Vektör araması, `src/chunker.py` içindeki parça boyutu ve örtüşme ayarlarıyla
çalışır; Chroma veritabanı proje içindeki `data/chroma/` dizininde tutulur.

## Yapı
- `src/pdf_loader.py`: PDF'ten sayfa sayfa metin
- `src/chunker.py`: metni parçalara bölme
- `src/vector_store.py`: embedding + ChromaDB + arama
- `src/llm_client.py`: Ollama LLM bağlantısı
- `src/rag.py`: kaynağa dayalı cevap üretme
- `tests/`: her aşama için küçük testler
