"""
valuation.py — Çok Kaynaklı Web Scrape + Gemini 2.5 Flash ile Değerleme Raporu
================================================================================
Ücretsiz. API key gerektirmez (scraping tarafı).

Kaynaklar (öncelik sırasıyla):
  1. HepsiEmlak   — direkt HTML scrape
  2. Zingat        — direkt HTML scrape
  3. Emlakjet      — direkt HTML scrape
  4. Sahibinden    — DDG proxy araması (bot korumalı)
  5. Endeksa       — DDG proxy araması
  6. Genel DDG     — 8+ farklı sorgu + komşu mahalleler

İstatistik:
  - IQR yöntemi ile aykırı değer temizleme
  - Medyan + Ortalama ayrı hesap
  - m² girilmişse → birim fiyat (TL/m²) gerçek veriden üretilir

Gemini:
  - GEMINI_API_KEY  ← aistudio.google.com (ücretsiz, 1500/gün)
  - Model: gemini-2.5-flash

Arayüz (app.py ile uyumlu):
  from valuation import generate_valuation_report, valuation_status
================================================================================
"""

import os
import re
import json
import time
import statistics
import requests
from urllib.parse import quote
from datetime import datetime
from bs4 import BeautifulSoup
from google import genai

# ─────────────────────────────────────────────────────────────────────────────
# Konfigürasyon
# ─────────────────────────────────────────────────────────────────────────────
GEMINI_MODEL   = "gemini-2.5-flash"
SCRAPE_TIMEOUT = 14
MAX_RESULTS    = 40   # toplam veri seti büyüklüğü
MAX_CONTEXT    = 30   # Gemini'ye gönderilecek ilan sayısı

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.5",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT":             "1",
    "Connection":      "keep-alive",
}

# Komşu mahalle haritası — arama genişletmek için
NEIGHBOR_MAP: dict[str, list[str]] = {
    "dikmen":        ["kavaklıdere", "çukurambar", "balgat"],
    "çankaya":       ["kavaklıdere", "gaziosmanpaşa", "ayrancı"],
    "kavaklıdere":   ["çankaya", "dikmen", "gaziosmanpaşa"],
    "batıkent":      ["elvankent", "öveçler", "törekent"],
    "keçiören":      ["etlik", "kalaba", "bağlum"],
    "mamak":         ["altındağ", "tuzluçayır", "mamak"],
    "etimesgut":     ["eryaman", "elvankent", "sincan"],
    "eryaman":       ["etimesgut", "elvankent", "törekent"],
    "gaziosmanpaşa": ["kavaklıdere", "çankaya", "ayrancı"],
    "ayrancı":       ["çankaya", "gaziosmanpaşa", "kavaklıdere"],
    "balgat":        ["dikmen", "çukurambar", "söğütözü"],
    "çukurambar":    ["balgat", "söğütözü", "diplomatik site"],
    "öveçler":       ["batıkent", "demetevler", "yenimahalle"],
    "sincan":        ["etimesgut", "eryaman", "elvankent"],
    "pursaklar":     ["keçiören", "altındağ"],
    "gölbaşı":       ["çankaya", "balgat"],
}


# ─────────────────────────────────────────────────────────────────────────────
# Yardımcılar
# ─────────────────────────────────────────────────────────────────────────────
def _fmt(val: int) -> str:
    return f"{val:,}".replace(",", ".") + " TL"


def _neighbors(neighborhood: str) -> list[str]:
    key = neighborhood.lower().strip()
    for k, v in NEIGHBOR_MAP.items():
        if k in key or key in k:
            return v
    return []


def _pt_slug(property_type: str) -> dict:
    """Mülk tipinden kaynak-bazlı URL segmentleri üret."""
    pt = property_type.lower()
    if "daire" in pt:
        return {"he": "daire-satilik", "zingat": "daire", "ej": "daire"}
    if "villa" in pt:
        return {"he": "villa-satilik", "zingat": "villa", "ej": "villa"}
    if "arsa" in pt:
        return {"he": "arsa-satilik", "zingat": "arsa", "ej": "arsa"}
    if "dükkan" in pt or "ofis" in pt or "işyeri" in pt:
        return {"he": "isyeri-satilik", "zingat": "isyeri", "ej": "isyeri"}
    if "müstakil" in pt or "ev" in pt:
        return {"he": "mustakil-ev-satilik", "zingat": "mustakil-ev", "ej": "mustakil-ev"}
    return {"he": "konut-satilik", "zingat": "konut", "ej": "konut"}


def valuation_status() -> dict:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    return {
        "ok":         bool(key),
        "configured": bool(key),
        "model":      GEMINI_MODEL,
        "provider":   "gemini+multiscrape",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Fiyat / m² regex
# ─────────────────────────────────────────────────────────────────────────────
_PRICE_RE = re.compile(
    r"(\d{1,3}(?:[.,]\d{3})+)\s*(?:TL|tl|₺)"
    r"|(\d+(?:[.,]\d+)?)\s*milyon\s*(?:TL|tl|₺)?",
    re.IGNORECASE,
)
_SQM_RE = re.compile(r"(\d{2,4})\s*m[²2]", re.IGNORECASE)


def _parse_price(text: str) -> int | None:
    for m in _PRICE_RE.finditer(text):
        try:
            if m.group(2):
                val = float(m.group(2).replace(",", ".")) * 1_000_000
            else:
                val = int(re.sub(r"[^\d]", "", m.group(1)))
            if 300_000 <= val <= 200_000_000:
                return int(val)
        except (ValueError, TypeError):
            pass
    return None


def _parse_sqm(text: str) -> int | None:
    m = _SQM_RE.search(text)
    if m:
        v = int(m.group(1))
        return v if 20 <= v <= 2000 else None
    return None


def _extract_prices(results: list[dict]) -> list[int]:
    prices = []
    for r in results:
        text = r.get("title", "") + " " + r.get("snippet", "")
        p = _parse_price(text)
        if p:
            prices.append(p)
    return sorted(prices)


def _iqr_clean(prices: list[int]) -> list[int]:
    """IQR yöntemiyle aykırı değerleri temizle (1.5×IQR kuralı)."""
    if len(prices) < 4:
        return prices
    s  = sorted(prices)
    n  = len(s)
    q1 = statistics.median(s[:n // 2])
    q3 = statistics.median(s[(n + 1) // 2:])
    iqr = q3 - q1
    if iqr == 0:
        return s
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    cleaned = [p for p in s if lo <= p <= hi]
    removed = len(s) - len(cleaned)
    if removed:
        print(f"   IQR temizleme: {removed} aykırı değer çıkarıldı")
    return cleaned if cleaned else s


def _stats(prices: list[int], sqm: str = "") -> dict:
    """Fiyat listesinden istatistik üret."""
    if not prices:
        return {}
    clean = _iqr_clean(prices)
    avg   = int(statistics.mean(clean))
    med   = int(statistics.median(clean))
    lo    = min(clean)
    hi    = max(clean)
    out   = {
        "count":     len(clean),
        "raw_count": len(prices),
        "min":       lo,
        "max":       hi,
        "average":   avg,
        "median":    med,
    }
    sqm_int = None
    try:
        sqm_int = int(re.sub(r"[^\d]", "", sqm)) if sqm else None
    except Exception:
        pass
    if sqm_int and sqm_int > 0:
        out["per_sqm_avg"] = int(avg / sqm_int)
        out["per_sqm_med"] = int(med / sqm_int)
        out["per_sqm_min"] = int(lo  / sqm_int)
        out["per_sqm_max"] = int(hi  / sqm_int)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Scraper 1 — DuckDuckGo HTML
# ─────────────────────────────────────────────────────────────────────────────
def _ddg(query: str, max_r: int = 10) -> list[dict]:
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "kl": "tr-tr", "s": "0"},
            headers=HEADERS,
            timeout=SCRAPE_TIMEOUT,
        )
        if not resp.ok:
            return []
        soup    = BeautifulSoup(resp.text, "html.parser")
        results = []
        for el in soup.select(".result")[:max_r]:
            title   = el.select_one(".result__title")
            snippet = el.select_one(".result__snippet")
            url     = el.select_one(".result__url")
            t = title.get_text(strip=True)   if title   else ""
            s = snippet.get_text(strip=True) if snippet else ""
            u = url.get_text(strip=True)     if url     else ""
            if t or s:
                results.append({"title": t, "snippet": s, "url": u, "source": "ddg"})
        print(f"   DDG '{query[:55]}' → {len(results)} sonuç")
        return results
    except Exception as e:
        print(f"   ⚠ DDG '{query[:40]}': {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Scraper 2 — HepsiEmlak
# ─────────────────────────────────────────────────────────────────────────────
def _scrape_hepsiemlak(neighborhood: str, property_type: str) -> list[dict]:
    slugs = _pt_slug(property_type)
    cat   = slugs["he"]
    loc   = quote(f"{neighborhood.lower()}-ankara")
    url   = f"https://www.hepsiemlak.com/{cat}?location_slug={loc}"
    results = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=SCRAPE_TIMEOUT)
        if not resp.ok:
            print(f"   ⚠ HepsiEmlak HTTP {resp.status_code}")
            return []
        soup  = BeautifulSoup(resp.text, "html.parser")
        cards = []
        for sel in ["li.listing-item", ".listing-card", "article.listing",
                    "[class*=listingCard]", "[class*=listing-item]"]:
            cards = soup.select(sel)
            if cards:
                break
        for card in cards[:15]:
            full_text = card.get_text(" ", strip=True)
            price = _parse_price(full_text)
            if not price:
                continue
            title_el = card.select_one(".listing-title,.title,h2,h3,[class*=title]")
            title    = title_el.get_text(strip=True) if title_el else f"{neighborhood} {property_type}"
            results.append({
                "title":   title,
                "snippet": full_text[:300],
                "url":     "hepsiemlak.com",
                "source":  "hepsiemlak",
                "price":   price,
            })
        print(f"   HepsiEmlak → {len(results)} ilan")
    except Exception as e:
        print(f"   ⚠ HepsiEmlak hatası: {e}")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Scraper 3 — Zingat
# ─────────────────────────────────────────────────────────────────────────────
def _scrape_zingat(neighborhood: str, property_type: str) -> list[dict]:
    slugs = _pt_slug(property_type)
    cat   = slugs["zingat"]
    loc   = neighborhood.lower().replace(" ", "-")
    url   = f"https://www.zingat.com/ankara/{loc}/{cat}-satilik"
    results = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=SCRAPE_TIMEOUT)
        if not resp.ok:
            print(f"   ⚠ Zingat HTTP {resp.status_code}")
            return []
        soup  = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(
            ".listing-card,.property-item,[class*=listing],[class*=property-card]"
        )
        for card in cards[:15]:
            full_text = card.get_text(" ", strip=True)
            price = _parse_price(full_text)
            if not price:
                continue
            title_el = card.select_one("h2,h3,.title,[class*=title]")
            title    = title_el.get_text(strip=True) if title_el else f"{neighborhood} {property_type}"
            results.append({
                "title":   title,
                "snippet": full_text[:300],
                "url":     "zingat.com",
                "source":  "zingat",
                "price":   price,
            })
        print(f"   Zingat → {len(results)} ilan")
    except Exception as e:
        print(f"   ⚠ Zingat hatası: {e}")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Scraper 4 — Emlakjet
# ─────────────────────────────────────────────────────────────────────────────
def _scrape_emlakjet(neighborhood: str, property_type: str) -> list[dict]:
    pt  = property_type.lower()
    cat = ("daire"    if "daire"  in pt else
           "villa"    if "villa"  in pt else
           "arsa"     if "arsa"   in pt else "konut")
    loc = neighborhood.lower().replace(" ", "-")
    url = f"https://www.emlakjet.com/satilik-{cat}/ankara/{loc}/"
    results = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=SCRAPE_TIMEOUT)
        if not resp.ok:
            print(f"   ⚠ Emlakjet HTTP {resp.status_code}")
            return []
        soup  = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("[class*=listing],[class*=card],[class*=ilan],article")
        for card in cards[:15]:
            full_text = card.get_text(" ", strip=True)
            price = _parse_price(full_text)
            if not price:
                continue
            title_el = card.select_one("h2,h3,.title,[class*=title]")
            title    = title_el.get_text(strip=True) if title_el else f"{neighborhood} {property_type}"
            results.append({
                "title":   title,
                "snippet": full_text[:300],
                "url":     "emlakjet.com",
                "source":  "emlakjet",
                "price":   price,
            })
        print(f"   Emlakjet → {len(results)} ilan")
    except Exception as e:
        print(f"   ⚠ Emlakjet hatası: {e}")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Toplu Arama Koordinatörü
# ─────────────────────────────────────────────────────────────────────────────
def _multi_search(
    neighborhood: str,
    property_type: str,
    sqm: str = "",
) -> list[dict]:
    """Tüm kaynaklardan veri toplar, tekilleştirir ve döndürür."""
    all_results: list[dict] = []
    seen: set[str]          = set()

    def _add(items: list[dict]) -> None:
        for r in items:
            key = (r.get("url", "") + r.get("title", ""))[:120]
            if key not in seen:
                seen.add(key)
                all_results.append(r)

    # ── 1. Direkt Scraperlar ──────────────────────────────────────────────────
    _add(_scrape_hepsiemlak(neighborhood, property_type))
    time.sleep(0.5)
    _add(_scrape_zingat(neighborhood, property_type))
    time.sleep(0.5)
    _add(_scrape_emlakjet(neighborhood, property_type))
    time.sleep(0.4)

    # ── 2. DDG — ana mahalle sorguları ───────────────────────────────────────
    ddg_queries = [
        f"site:sahibinden.com {neighborhood} ankara {property_type} satılık",
        f"site:hepsiemlak.com {neighborhood} ankara {property_type} satılık",
        f"{neighborhood} ankara {property_type} satılık fiyat 2025",
        f"{neighborhood} ankara m2 fiyatı emlak 2025",
        f"sahibinden.com {neighborhood} {property_type} satılık TL",
        f"endeksa.com {neighborhood} ankara konut fiyat",
        f"{neighborhood} ankara {property_type} ortalama fiyat",
        f"zingat.com {neighborhood} ankara {property_type}",
    ]
    for q in ddg_queries:
        _add(_ddg(q, max_r=10))
        time.sleep(0.35)

    # ── 3. DDG — komşu mahalleler (bağlam zenginleştirme) ────────────────────
    for nb in _neighbors(neighborhood)[:2]:
        _add(_ddg(f"{nb} ankara {property_type} satılık fiyat 2025", max_r=6))
        time.sleep(0.3)

    # ── 4. m² girilmişse birim fiyat araması ─────────────────────────────────
    if sqm:
        _add(_ddg(f"{neighborhood} ankara {sqm}m2 {property_type} satılık", max_r=8))
        time.sleep(0.3)

    # ── 5. Ankara genel piyasa bağlamı ───────────────────────────────────────
    _add(_ddg(f"ankara {property_type} ortalama m2 fiyatı 2025", max_r=6))

    print(f"\n   📊 Toplam kayıt: {len(all_results)}")
    return all_results[:MAX_RESULTS]


# ─────────────────────────────────────────────────────────────────────────────
# Context Builder
# ─────────────────────────────────────────────────────────────────────────────
def _build_context(results: list[dict], st: dict, sqm: str = "") -> str:
    lines = []

    if st:
        lines += [
            f"=== GERÇEK PAZAR VERİSİ ({st['count']} ilan, {st['raw_count']} ham kayıt) ===",
            f"  Min    : {_fmt(st['min'])}",
            f"  Maks   : {_fmt(st['max'])}",
            f"  Ort    : {_fmt(st['average'])}",
            f"  Medyan : {_fmt(st['median'])}  ← aykırı değer etkisi az",
        ]
        if "per_sqm_avg" in st:
            lines += [
                f"  m²/Ort : {_fmt(st['per_sqm_avg'])}/m²",
                f"  m²/Med : {_fmt(st['per_sqm_med'])}/m²",
            ]
        lines.append("")

    # Kaynak bazlı özet
    by_source: dict[str, list[int]] = {}
    for r in results:
        src = r.get("source", "ddg")
        if r.get("price"):
            by_source.setdefault(src, []).append(r["price"])
    if by_source:
        lines.append("=== KAYNAK BAZLI FİYATLAR ===")
        for src, prices in by_source.items():
            avg_s = int(sum(prices) / len(prices))
            lines.append(f"  {src:15s} → {len(prices)} ilan | Ort: {_fmt(avg_s)}")
        lines.append("")

    lines.append("=== İLAN DETAYLARI ===")
    ranked = sorted(results, key=lambda r: 0 if r.get("price") else 1)
    for i, r in enumerate(ranked[:MAX_CONTEXT], 1):
        price_tag = f" [{_fmt(r['price'])}]" if r.get("price") else ""
        lines.append(f"{i}. [{r.get('source','?')}]{price_tag} {r['title']}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet'][:220]}")

    return "\n".join(lines) or "Yeterli veri bulunamadı."


# ─────────────────────────────────────────────────────────────────────────────
# Prompt Builder
# ─────────────────────────────────────────────────────────────────────────────
def _build_prompt(
    name: str, neighborhood: str, property_type: str,
    rooms: str, sqm: str, notes: str,
    context: str, st: dict,
) -> str:
    now    = datetime.now().strftime("%d.%m.%Y %H:%M")
    extras = []
    if rooms: extras.append(f"- Oda Sayısı  : {rooms}")
    if sqm:   extras.append(f"- Metrekare   : {sqm} m²")
    if notes: extras.append(f"- Notlar      : {notes}")
    extra_block = "\n".join(extras) if extras else "- (ek bilgi girilmedi)"

    if st and st.get("count", 0) >= 3:
        med   = st["median"]
        avg   = st["average"]
        lo    = st["min"]
        hi    = st["max"]
        count = st["count"]
        price_directive = f"""
⚠⚠ ZORUNLU — Aşağıdaki gerçek pazar verisini MUTLAKA kullan:
   Kaynak: {count} ilan (IQR ile aykırı değerler temizlenmiş)
   Min    = {_fmt(lo)}
   Maks   = {_fmt(hi)}
   Ort    = {_fmt(avg)}
   Medyan = {_fmt(med)}  ← EN GÜVENİLİR referans"""
        if "per_sqm_avg" in st:
            price_directive += f"""
   m²/Ort = {_fmt(st['per_sqm_avg'])}/m²
   m²/Med = {_fmt(st['per_sqm_med'])}/m²"""
        price_directive += f"""
   → price_range.min     = {_fmt(int(lo * 0.93))}
   → price_range.max     = {_fmt(int(hi * 1.07))}
   → price_range.average = {_fmt(avg)}
   → price_range.median  = {_fmt(med)}"""
    else:
        price_directive = """
⚠ Yeterli ilan verisi bulunamadı. 2025 Ankara piyasası ve mahalle
   özelliklerine dayanarak gerçekçi tahmin yap. data_quality = "tahmini" yaz."""

    return f"""Sen Türkiye'nin en deneyimli gayrimenkul değerleme uzmanısın.
Ankara {neighborhood} bölgesinde {property_type} için gerçek web ilanlarından
derlenen verilerle kapsamlı bir değerleme raporu hazırlıyorsun.

══════════════ MÜŞTERİ ══════════════
Ad    : {name}
Bölge : {neighborhood}, Ankara
Mülk  : {property_type}
{extra_block}
Tarih : {now}

══════════════ PAZAR VERİSİ KILAVUZU ══════════════{price_directive}

══════════════ WEB KAYNAKLARINDAN DERLENEN VERİ ══════════════
{context}
══════════════════════════════════════════════════════════════

KURALLAR:
1. Yalnızca geçerli bir JSON objesi döndür — markdown, açıklama, kod bloğu yok.
2. Tüm metinler Türkçe.
3. Fiyatları TL, binlik nokta ayraçlı (örn: "4.750.000 TL").
4. Gerçek veri varsa onu kullan; tahmin yaparsan "tahmini" ibaresini ekle.
5. executive_summary'de {name}'e doğrudan hitap et.
6. investment_score 1-10 tam sayı.
7. pros ≥ 3 madde, cons ≥ 2 madde.
8. data_quality: "gercek" (≥3 gerçek ilan) veya "tahmini".

JSON YAPISI:
{{
  "price_range": {{
    "min":          "X.XXX.XXX TL",
    "max":          "X.XXX.XXX TL",
    "average":      "X.XXX.XXX TL",
    "median":       "X.XXX.XXX TL",
    "per_sqm_min":  "XX.XXX TL/m²",
    "per_sqm_max":  "XX.XXX TL/m²",
    "per_sqm_avg":  "XX.XXX TL/m²",
    "data_quality": "gercek",
    "source_count": {st.get("count", 0)}
  }},
  "neighborhood_analysis": {{
    "summary":     "2-3 cümle",
    "pros":        ["avantaj1","avantaj2","avantaj3"],
    "cons":        ["dezavantaj1","dezavantaj2"],
    "trend":       "yükselen",
    "trend_detail":"1-2 cümle"
  }},
  "investment_score": {{
    "score":    8,
    "max":      10,
    "label":    "Çok İyi",
    "reasoning":"2-3 cümle"
  }},
  "market_comparison": {{
    "vs_district":           "1-2 cümle",
    "vs_ankara":             "1-2 cümle",
    "similar_neighborhoods": ["mahalle1","mahalle2","mahalle3"]
  }},
  "key_factors": [
    {{"factor":"Başlık","impact":"positive","detail":"Açıklama"}},
    {{"factor":"Başlık","impact":"positive","detail":"Açıklama"}},
    {{"factor":"Başlık","impact":"negative","detail":"Açıklama"}},
    {{"factor":"Başlık","impact":"neutral", "detail":"Açıklama"}}
  ],
  "valuation_tips":    ["tavsiye1","tavsiye2","tavsiye3"],
  "web_sources":       ["hepsiemlak.com","zingat.com","sahibinden.com","emlakjet.com","endeksa.com"],
  "executive_summary": "Müşteriye ({name}) hitaben 3-4 cümle özet",
  "disclaimer":        "Bu rapor yapay zeka destekli ön değerleme amaçlıdır ve hukuki bağlayıcılığı yoktur. Kesin değerleme için yetkili SPK lisanslı ekspertiz önerilir."
}}"""


# ─────────────────────────────────────────────────────────────────────────────
# JSON Çıkarıcı
# ─────────────────────────────────────────────────────────────────────────────
def _extract_json(raw: str) -> str:
    if "```" in raw:
        for part in raw.split("```"):
            p = part.strip()
            if p.lower().startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                raw = p
                break
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    return raw[start:end] if start != -1 and end > start else raw


# ─────────────────────────────────────────────────────────────────────────────
# Ana Fonksiyon
# ─────────────────────────────────────────────────────────────────────────────
def generate_valuation_report(
    name: str,
    neighborhood: str,
    property_type: str,
    rooms: str = "",
    sqm: str   = "",
    notes: str = "",
) -> dict:
    """
    Çok kaynaklı web scrape + Gemini 2.5 Flash ile Ankara gayrimenkul değerleme.
    grok.py / gemini.py ile birebir aynı dönüş arayüzü.

    Returns:
        {"ok": True,  "report": {...}, "search_used": True, "listings_count": N}
        {"ok": False, "error": "..."}
    """
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return {"ok": False, "error": "GEMINI_API_KEY tanımlı değil"}

    t0 = time.time()
    print(f"\n{'='*60}")
    print(f"🏠 Değerleme: {neighborhood} / {property_type}")
    if sqm:   print(f"   m²  : {sqm}")
    if rooms: print(f"   Oda : {rooms}")
    print(f"{'='*60}")

    # ── 1. Veri Toplama ───────────────────────────────────────────────────────
    results    = _multi_search(neighborhood, property_type, sqm)
    raw_prices = _extract_prices(results)

    # Scraper'ların "price" alanını da ekle
    for r in results:
        if r.get("price") and r["price"] not in raw_prices:
            raw_prices.append(r["price"])
    raw_prices = sorted(set(raw_prices))

    st = _stats(raw_prices, sqm)
    print(f"\n   📈 Ham fiyat : {len(raw_prices)} | Temizlenmiş : {st.get('count', 0)}")
    if st:
        print(f"   💰 Medyan   : {_fmt(st['median'])}")
        print(f"   💰 Ortalama : {_fmt(st['average'])}")
        if "per_sqm_avg" in st:
            print(f"   📐 m²/Ort   : {_fmt(st['per_sqm_avg'])}/m²")

    # ── 2. Context ────────────────────────────────────────────────────────────
    context = _build_context(results, st, sqm)

    # ── 3. Gemini ─────────────────────────────────────────────────────────────
    prompt = _build_prompt(
        name, neighborhood, property_type, rooms, sqm, notes, context, st
    )
    try:
        print(f"\n🤖 {GEMINI_MODEL} analiz ediyor...")
        client   = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model    = GEMINI_MODEL,
            contents = prompt,
        )
        raw_text = response.text.strip()
    except Exception as e:
        return {"ok": False, "error": f"Gemini hatası: {e}"}

    # ── 4. JSON Parse ─────────────────────────────────────────────────────────
    raw_text = _extract_json(raw_text)
    try:
        report = json.loads(raw_text)
    except json.JSONDecodeError as e:
        print(f"❌ JSON parse hatası: {e}\n{raw_text[:500]}")
        return {"ok": False, "error": f"JSON parse hatası: {e}"}

    # ── 5. Gerçek İstatistikle Fiyat Alanını Güvenle Üzerine Yaz ─────────────
    if st and st["count"] >= 3:
        pr = report.setdefault("price_range", {})
        pr["average"]      = _fmt(st["average"])
        pr["median"]       = _fmt(st["median"])
        pr["min"]          = _fmt(int(st["min"] * 0.93))
        pr["max"]          = _fmt(int(st["max"] * 1.07))
        pr["source_count"] = st["count"]
        pr["data_quality"] = "gercek"
        if "per_sqm_avg" in st:
            pr["per_sqm_avg"] = _fmt(st["per_sqm_avg"]) + "/m²"
            pr["per_sqm_min"] = _fmt(st["per_sqm_min"]) + "/m²"
            pr["per_sqm_max"] = _fmt(st["per_sqm_max"]) + "/m²"

    # ── 6. Meta ───────────────────────────────────────────────────────────────
    report["generated_at"]    = datetime.now().strftime("%d.%m.%Y %H:%M")
    report["neighborhood"]     = neighborhood
    report["property_type"]    = property_type
    report["model"]            = GEMINI_MODEL
    report["search_used"]      = len(results) > 0
    report["listings_count"]   = st.get("count", 0)
    report["raw_price_count"]  = len(raw_prices)
    report.setdefault(
        "web_sources",
        ["hepsiemlak.com", "zingat.com", "sahibinden.com", "emlakjet.com", "endeksa.com"],
    )

    elapsed = round(time.time() - t0, 1)
    pr      = report.get("price_range", {})
    print(f"\n✅ Tamamlandı [{elapsed}s]")
    print(f"   Medyan    : {pr.get('median', '?')}")
    print(f"   Ortalama  : {pr.get('average', '?')}")
    print(f"   Veri sayısı: {st.get('count', 0)} temizlenmiş / {len(raw_prices)} ham")

    return {
        "ok":             True,
        "report":         report,
        "search_used":    len(results) > 0,
        "listings_count": st.get("count", 0),
    }
