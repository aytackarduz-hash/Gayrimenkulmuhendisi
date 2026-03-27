#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════╗
║  CB.COM.TR İLAN SCRAPER  —  Erdoğan Işık / CB ÇİZGİ    ║
║  • Liste sayfasından ilan URL'lerini çeker               ║
║  • Her detail sayfasına girer, tüm görselleri alır      ║
║  • Nominatim ile koordinat belirler                      ║
║  • Tek bağımsız HTML üretir (slider + harita pop-up)     ║
╚══════════════════════════════════════════════════════════╝

Kullanım:
    pip install requests beautifulsoup4 lxml
    python3 cb_scraper.py

Çıktı: cb_ilanlar.html
"""

import json
import re
import sys
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ─── HEDEF URL ────────────────────────────────────────────────────────────────
TARGET_URL  = "https://www.cb.com.tr/ilanlar?officeid=372&officeuserid=18631"
BASE_URL    = "https://www.cb.com.tr"
OUTPUT_FILE = "cb_ilanlar.html"

# ─── ANKARA SEMT LİSTESİ (başlık → koordinat için) ───────────────────────────
ANKARA_SEMTLER = [
    "Dikmen", "Kızılay", "Bahçelievler", "Çankaya", "Keçiören",
    "Mamak", "Etimesgut", "Sincan", "Gölbaşı", "Pursaklar",
    "Altındağ", "Yenimahalle", "Eryaman", "Batıkent", "Ostim",
    "Ümitköy", "Konutkent", "Çayyolu", "Balgat", "Tunalı",
    "Kocatepe", "Sıhhiye", "Ulus", "Beştepe", "İncek",
    "Angora", "Naci Çakır", "Aziziye", "Ayrancı", "Gaziosmanpaşa",
]
DIKMEN_LAT, DIKMEN_LNG = 39.884, 32.863

# ─── HEADERS ──────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.5",
}

# ─── GEOCODİNG ────────────────────────────────────────────────────────────────
_coord_cache: dict = {}
_last_nominatim_call: float = 0.0
_TR_MAP = str.maketrans("çğışöüÇĞİŞÖÜ", "cgisouCGISOu")


def _normalize(text: str) -> str:
    return text.translate(_TR_MAP).upper()


def geocode_query(query: str) -> Optional[tuple]:
    global _last_nominatim_call
    if query in _coord_cache:
        return _coord_cache[query]
    elapsed = time.time() - _last_nominatim_call
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": query, "format": "json", "limit": 1,
                "countrycodes": "tr",
                "viewbox": "32.5,40.1,33.2,39.6", "bounded": 1,
            },
            headers={"User-Agent": "DikmenEliteGayrimenkul/1.0 (erdogan@cb.com.tr)"},
            timeout=8,
        )
        _last_nominatim_call = time.time()
        data = resp.json()
        if data:
            lat, lon = float(data[0]["lat"]), float(data[0]["lon"])
            _coord_cache[query] = (lat, lon)
            return lat, lon
    except Exception as e:
        print(f"  ⚠ Geocode hatası: {e}")
    _coord_cache[query] = None
    return None


def extract_location_from_title(title: str) -> Optional[str]:
    title_norm = _normalize(title)
    matches = [s for s in ANKARA_SEMTLER if _normalize(s) in title_norm]
    if not matches:
        return None
    return f"{max(matches, key=len)}, Ankara, Türkiye"


def get_listing_coords(title: str, loc: str) -> tuple:
    for q in [
        extract_location_from_title(title),
        f"{loc}, Ankara, Türkiye" if loc and loc != "Ankara" else None,
        "Çankaya, Ankara, Türkiye",
    ]:
        if q:
            coords = geocode_query(q)
            if coords:
                return coords
    return DIKMEN_LAT, DIKMEN_LNG


# ─── YARDIMCI ─────────────────────────────────────────────────────────────────

def clean(el) -> str:
    return el.get_text(strip=True) if el else ""


def fetch_html(url: str, retries: int = 2) -> Optional[BeautifulSoup]:
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 200:
                return BeautifulSoup(r.content, "lxml")
            print(f"  HTTP {r.status_code}")
        except Exception as e:
            print(f"  ⚠ Fetch hatası (deneme {attempt+1}): {e}")
            if attempt < retries:
                time.sleep(2)
    return None


# ─── DETAY SAYFA SCRAPER ──────────────────────────────────────────────────────

def scrape_detail(url: str) -> dict:
    """
    İlan detay sayfasından:
      • Tüm slider görselleri
      • Özellik tablosu (m², oda, kat, ısıtma, …)
      • Açıklama metni
      • Danışman adı / fotoğrafı / ofisi
    """
    soup = fetch_html(url)
    result: dict = {"detail_url": url, "images": [], "features": [], "description": ""}
    if not soup:
        return result

    # ── Görseller ────────────────────────────────────────────────────────────
    # Slider / carousel yapıları
    for sel in [
        "div.swiper-slide img",
        "div.slick-slide img",
        "div.carousel-item img",
        ".detail-slider img",
        ".stock-slider img",
        ".cb-detail-slider img",
        "figure img",
    ]:
        imgs = soup.select(sel)
        if imgs:
            for img in imgs:
                src = img.get("src") or img.get("data-src") or img.get("data-lazy") or ""
                src = src.strip()
                if src and "placeholder" not in src and src not in result["images"]:
                    if src.startswith("/"):
                        src = BASE_URL + src
                    result["images"].append(src)
            if result["images"]:
                break

    # Eğer slider bulunamadıysa sayfadaki tüm media.cb img'leri al
    if not result["images"]:
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            src = src.strip()
            if "media.cb" in src or "StockMedia" in src:
                if src not in result["images"]:
                    result["images"].append(src)

    # ── Özellik tablosu ──────────────────────────────────────────────────────
    feats: list[dict] = []
    # Tablo satırları
    for row in soup.select("table tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) >= 2:
            k = clean(cells[0])
            v = clean(cells[1])
            if k and v and len(k) < 50:
                feats.append({"label": k, "value": v})

    # dt/dd çiftleri
    dts = soup.find_all("dt")
    dds = soup.find_all("dd")
    for dt, dd in zip(dts, dds):
        k, v = clean(dt), clean(dd)
        if k and v:
            feats.append({"label": k, "value": v})

    # li içinde ":" olan satırlar
    for li in soup.select("ul.features li, .property-features li, .cb-features li"):
        txt = clean(li)
        if ":" in txt and len(txt) < 80:
            parts = txt.split(":", 1)
            feats.append({"label": parts[0].strip(), "value": parts[1].strip()})

    # Tekrar edenleri temizle
    seen_keys: set = set()
    for f in feats:
        key = f["label"].lower()
        if key not in seen_keys:
            seen_keys.add(key)
            result["features"].append(f)
    result["features"] = result["features"][:20]

    # ── m² / oda hızlı yakalama ──────────────────────────────────────────────
    page_text = soup.get_text(" ", strip=True)
    if not any(f["label"].lower() in ("oda", "oda sayısı") for f in result["features"]):
        m = re.search(r"(\d+\+\d+|\d+\+0)", page_text)
        if m:
            result["rooms"] = m.group(1)
    if not any("m²" in f["value"] or "m2" in f["value"] for f in result["features"]):
        m = re.search(r"(\d+)\s*m[²2]", page_text)
        if m:
            result["sqm"] = m.group(1) + " m²"

    # ── Açıklama ─────────────────────────────────────────────────────────────
    for sel in [".description", ".ilan-aciklama", ".detail-description",
                "#aciklama", "[itemprop='description']"]:
        el = soup.select_one(sel)
        if el:
            result["description"] = el.get_text(" ", strip=True)[:600]
            break

    # ── Danışman ─────────────────────────────────────────────────────────────
    agent_link = soup.select_one("a[href*='/danismanlar/']")
    if agent_link:
        result["agent_name"] = clean(agent_link)
    agent_img = soup.select_one("img[src*='OfficeUser']")
    if agent_img:
        src = agent_img.get("src", "")
        result["agent_img"] = BASE_URL + src if src.startswith("/") else src
    office_link = soup.select_one("a[href*='/ofisler/']")
    if office_link:
        result["agent_office"] = clean(office_link)

    return result


# ─── LİSTE SAYFA SCRAPER ──────────────────────────────────────────────────────

def scrape_listings() -> list[dict]:
    print(f"\n{'='*60}")
    print(f"  CB.COM.TR İLAN SCRAPER")
    print(f"  Kaynak: {TARGET_URL}")
    print(f"{'='*60}\n")

    print("📡 [1/3] Liste sayfası çekiliyor…")
    soup = fetch_html(TARGET_URL)
    if not soup:
        print("❌ Liste sayfası alınamadı.")
        sys.exit(1)

    # ── Kart seçicileri ──────────────────────────────────────────────────────
    # cb.com.tr'nin gerçek sınıf adları
    cards = (
        soup.select(".cb-list-item") or
        soup.select("article.list-item") or
        soup.select(".property-card") or
        soup.select("[class*='list-item']")
    )

    # Eğer kart bulunamazsa: tüm ilan linklerini regex ile çek
    if not cards:
        print("  ⚠ Kart seçici bulunamadı — link regex yöntemi deneniyor…")
        all_links = soup.find_all("a", href=True)
        detail_urls = []
        for a in all_links:
            href = a["href"]
            if re.search(r"/(satilik|kiralik|gunluk-kiralik)/\w+/\d{4,}$", href):
                full = BASE_URL + href if href.startswith("/") else href
                if full not in detail_urls:
                    detail_urls.append(full)

        # Kart bilgilerini link parent'tan topla
        raw: list[dict] = []
        for url in detail_urls:
            link_el = soup.find("a", href=lambda h: h and url.endswith(h.strip("/")))
            title = ""
            img_url = ""
            price = ""
            loc = "Ankara"

            # title: yakındaki h2/h3
            if link_el:
                parent = link_el.parent
                for _ in range(5):
                    if parent is None:
                        break
                    h = parent.find(["h2", "h3"])
                    if h:
                        title = clean(h)
                        break
                    price_el = parent.find(class_=re.compile("price|fiyat", re.I))
                    if price_el:
                        price = clean(price_el)
                    img = parent.find("img")
                    if img:
                        img_url = img.get("src") or img.get("data-src") or ""
                    parent = parent.parent

            pno = url.rstrip("/").split("/")[-1]
            raw.append({
                "portfolio_no": pno,
                "title": title or f"İlan #{pno}",
                "price": price,
                "img_thumb": img_url,
                "url": url,
                "loc": loc,
            })
        print(f"  {len(raw)} ilan linki bulundu (regex).")
    else:
        # ── Normal kart parse ────────────────────────────────────────────────
        raw: list[dict] = []
        for card in cards:
            try:
                title_el = (
                    card.select_one(".cb-list-item-info h2") or
                    card.select_one("h2") or card.select_one("h3")
                )
                title = clean(title_el)
                if not title:
                    continue

                price_el = (
                    card.select_one(".feature-item .text-primary") or
                    card.select_one("[class*='price']") or
                    card.select_one("[class*='fiyat']")
                )
                price = clean(price_el)

                link_el = (
                    card.select_one(".cb-list-img-container a") or
                    card.select_one("a[href]")
                )
                href = link_el["href"] if link_el else "#"
                full_url = BASE_URL + href if href.startswith("/") else href

                img_el = card.select_one("img")
                img_url = ""
                if img_el:
                    img_url = (img_el.get("src") or
                               img_el.get("data-src") or
                               img_el.get("data-lazy") or "")

                region_el = card.select_one('[itemprop="addressRegion"]')
                street_el = card.select_one('[itemprop="streetAddress"]')
                region = clean(region_el)
                street = clean(street_el)
                loc = f"{region} / {street}" if region and street else "Ankara"

                pno = full_url.rstrip("/").split("/")[-1]
                raw.append({
                    "portfolio_no": pno,
                    "title": title,
                    "price": price,
                    "img_thumb": img_url,
                    "url": full_url,
                    "loc": loc,
                })
            except Exception as e:
                print(f"  ⚠ Kart parse hatası: {e}")

        print(f"  {len(raw)} ilan kartı bulundu.")

    # ── Detay sayfalarını çek ─────────────────────────────────────────────────
    print(f"\n📋 [2/3] {len(raw)} detay sayfası çekiliyor…")
    listings: list[dict] = []

    for i, item in enumerate(raw, 1):
        print(f"  [{i}/{len(raw)}] {item['url']}")
        time.sleep(1.0)

        detail = scrape_detail(item["url"])

        # Görsel: detaydan yoksa thumbnail'ı kullan
        images = detail.get("images") or (
            [item["img_thumb"]] if item.get("img_thumb") else []
        )

        # Fiyat
        price = item.get("price") or ""
        if not price:
            full_text = " ".join(f["value"] for f in detail.get("features", []))
            m = re.search(r"([\d.,]+\s*(?:TL|₺|TRY|USD|EUR|GBP|\$|€|£))", full_text)
            if m:
                price = m.group(1)

        # Oda / m²
        rooms = detail.get("rooms", "")
        sqm   = detail.get("sqm", "")
        for f in detail.get("features", []):
            lbl = f["label"].lower()
            if not rooms and ("oda" in lbl or "room" in lbl):
                rooms = f["value"]
            if not sqm and ("m²" in lbl or "alan" in lbl or "brüt" in lbl):
                sqm = f["value"]

        # Tür / durum
        url_l = item["url"].lower()
        status = "Kiralık" if "kiralik" in url_l else "Satılık"
        path_parts = item["url"].rstrip("/").split("/")
        prop_type = path_parts[-2].replace("-", " ").title() if len(path_parts) >= 2 else "—"

        # Koordinat
        print(f"     🗺  Koordinat aranıyor: {item['loc']}")
        lat, lng = get_listing_coords(item["title"], item["loc"])

        listing = {
            "portfolio_no": item["portfolio_no"],
            "title":        item["title"],
            "url":          item["url"],
            "price":        price,
            "location":     item["loc"],
            "status":       status,
            "type":         prop_type,
            "rooms":        rooms,
            "sqm":          sqm,
            "images":       images,           # ← tüm slider görselleri
            "features":     detail.get("features", []),
            "description":  detail.get("description", ""),
            "agent_name":   detail.get("agent_name",  "Erdoğan Işık"),
            "agent_img":    detail.get("agent_img",
                "https://media.cb.com.tr/OfficeUserImages/3830/ERDOgAN-IsIK_HTKB8N5P81_75X75.jpg"),
            "agent_office": detail.get("agent_office", "CB ÇİZGİ"),
            "lat":          lat,
            "lng":          lng,
        }
        listings.append(listing)
        print(f"     ✓  {len(images)} görsel | lat={lat:.4f} lng={lng:.4f}")

    print(f"\n✅ [3/3] {len(listings)} ilan hazır. HTML üretiliyor…")
    return listings


# ─── HTML TEMPLATE ────────────────────────────────────────────────────────────
# (Tüm CSS + JS tek dosyaya gömülü)

HTML = r"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CB ÇİZGİ — İlan Portföyü</title>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@300;400;500;600&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<style>
/* ── BASE ── */
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --ink:#0c0c11;--surface:#141419;--card:#1b1b24;
  --border:rgba(255,255,255,.07);--border-gold:rgba(201,168,76,.45);
  --gold:#c9a84c;--gold-l:#e8c97a;--gold-dim:rgba(201,168,76,.13);
  --text:#e3dfd7;--muted:#6b6879;--white:#fff;
  --r:6px;--shadow:0 28px 72px rgba(0,0,0,.6);
}
html{scroll-behavior:smooth}
body{font-family:'DM Sans',sans-serif;background:var(--ink);color:var(--text);min-height:100vh;overflow-x:hidden}
body::after{content:'';position:fixed;inset:0;pointer-events:none;z-index:9999;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='4'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='1'/%3E%3C/svg%3E");
  opacity:.024;mix-blend-mode:overlay}

/* ── HEADER ── */
header{padding:50px 52px 36px;border-bottom:1px solid var(--border);position:relative;display:flex;align-items:center;gap:22px}
header::before{content:'';position:absolute;inset:0;background:linear-gradient(120deg,var(--gold-dim) 0%,transparent 55%);pointer-events:none}
header::after{content:'';position:absolute;bottom:-1px;left:52px;width:56px;height:2px;background:var(--gold)}
.hdr-avatar{width:54px;height:54px;border-radius:50%;border:1.5px solid var(--gold);object-fit:cover;flex-shrink:0}
.hdr-info h1{font-family:'Cormorant Garamond',serif;font-size:1.8rem;font-weight:500;color:var(--white);letter-spacing:.02em;line-height:1.1}
.hdr-info p{font-size:.7rem;letter-spacing:.14em;text-transform:uppercase;color:var(--gold);margin-top:5px}
.hdr-right{margin-left:auto;text-align:right}
.hdr-right .n{font-family:'Cormorant Garamond',serif;font-size:3.4rem;font-weight:300;color:rgba(201,168,76,.18);line-height:1}
.hdr-right .l{font-size:.6rem;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);margin-top:4px}

/* ── MAIN ── */
main{padding:48px 52px}
.sec-lbl{font-size:.62rem;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);margin-bottom:28px;display:flex;align-items:center;gap:12px}
.sec-lbl::after{content:'';flex:1;height:1px;background:var(--border)}

/* ── GRID ── */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:22px}

/* ── CARD ── */
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);overflow:hidden;cursor:pointer;
  transition:transform .38s cubic-bezier(.22,.61,.36,1),border-color .3s,box-shadow .38s;
  animation:fadeUp .55s ease both}
.card:nth-child(1){animation-delay:.04s}.card:nth-child(2){animation-delay:.12s}
.card:nth-child(3){animation-delay:.20s}.card:nth-child(4){animation-delay:.28s}
.card:nth-child(5){animation-delay:.36s}.card:nth-child(6){animation-delay:.44s}
@keyframes fadeUp{from{opacity:0;transform:translateY(22px)}to{opacity:1;transform:translateY(0)}}
.card:hover{transform:translateY(-7px);border-color:var(--gold);box-shadow:var(--shadow),0 0 0 1px var(--gold-dim)}

/* card image */
.card-img{position:relative;aspect-ratio:16/10;overflow:hidden;background:#111}
.card-img img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .5s cubic-bezier(.22,.61,.36,1)}
.card:hover .card-img img{transform:scale(1.06)}
.badge{position:absolute;top:12px;left:12px;background:var(--gold);color:var(--ink);font-size:.6rem;font-weight:500;letter-spacing:.12em;text-transform:uppercase;padding:5px 10px;border-radius:2px}
.photo-ct{position:absolute;bottom:10px;right:10px;background:rgba(0,0,0,.55);backdrop-filter:blur(6px);color:#fff;font-size:.68rem;padding:3px 8px;border-radius:2px;display:flex;align-items:center;gap:4px}

/* card body */
.card-body{padding:20px 22px}
.card-kind{font-size:.62rem;letter-spacing:.13em;text-transform:uppercase;color:var(--gold);margin-bottom:6px}
.card-title{font-family:'Cormorant Garamond',serif;font-size:1.1rem;font-weight:500;color:var(--white);line-height:1.35;margin-bottom:8px;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card-loc{display:flex;align-items:center;gap:5px;font-size:.75rem;color:var(--muted);margin-bottom:17px}
.card-loc svg{width:11px;height:11px;flex-shrink:0}
.card-meta{display:flex;gap:14px;flex-wrap:wrap;padding:13px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border);margin-bottom:15px}
.mi{display:flex;flex-direction:column;gap:2px}
.mi-l{font-size:.57rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.mi-v{font-size:.82rem;color:var(--text);font-weight:500}
.card-foot{display:flex;align-items:center;justify-content:space-between}
.price{font-family:'Cormorant Garamond',serif;font-size:1.45rem;font-weight:600;color:var(--gold-l)}
.pno{font-size:.58rem;color:var(--muted);margin-top:2px}
.arr{width:34px;height:34px;border-radius:50%;background:var(--gold-dim);border:1px solid var(--gold);
  display:flex;align-items:center;justify-content:center;transition:background .2s,transform .3s}
.card:hover .arr{background:var(--gold);transform:rotate(45deg)}
.arr svg{width:13px;height:13px;color:var(--gold);transition:color .2s}
.card:hover .arr svg{color:var(--ink)}

/* ── OVERLAY ── */
.overlay{position:fixed;inset:0;background:rgba(0,0,0,.85);backdrop-filter:blur(12px);
  z-index:500;display:flex;align-items:center;justify-content:center;padding:16px;
  opacity:0;pointer-events:none;transition:opacity .3s ease}
.overlay.open{opacity:1;pointer-events:all}

/* ── MODAL ── */
.modal{
  background:var(--surface);border:1px solid var(--border);border-radius:8px;
  width:100%;max-width:860px;max-height:95vh;overflow-y:auto;position:relative;
  transform:translateY(30px) scale(.97);
  transition:transform .42s cubic-bezier(.22,.61,.36,1);
  scrollbar-width:thin;scrollbar-color:var(--gold-dim) transparent;
  display:flex;flex-direction:column;
}
.overlay.open .modal{transform:translateY(0) scale(1)}
.modal::-webkit-scrollbar{width:4px}
.modal::-webkit-scrollbar-thumb{background:var(--gold-dim);border-radius:2px}

/* ── SLIDER ── */
.slider-wrap{position:relative;background:#000;flex-shrink:0;user-select:none}
.slider-track{display:flex;overflow:hidden;touch-action:pan-y}
.slide{min-width:100%;position:relative}
.slide img{width:100%;display:block;max-height:440px;object-fit:cover}
.slide-overlay{position:absolute;inset:0;background:linear-gradient(to bottom,transparent 55%,rgba(0,0,0,.55));pointer-events:none}

/* prev / next arrows */
.sl-btn{
  position:absolute;top:50%;transform:translateY(-50%);
  width:40px;height:40px;border-radius:50%;
  background:rgba(0,0,0,.55);backdrop-filter:blur(6px);
  border:1px solid rgba(255,255,255,.15);color:#fff;
  display:flex;align-items:center;justify-content:center;
  cursor:pointer;z-index:10;transition:background .2s,border-color .2s;
  font-size:1rem;
}
.sl-btn:hover{background:var(--gold);border-color:var(--gold);color:var(--ink)}
.sl-prev{left:14px}.sl-next{right:14px}

/* dots */
.sl-dots{
  position:absolute;bottom:12px;left:50%;transform:translateX(-50%);
  display:flex;gap:6px;z-index:10;
}
.sl-dot{width:6px;height:6px;border-radius:50%;background:rgba(255,255,255,.4);
  cursor:pointer;transition:background .2s,transform .2s;border:none;padding:0}
.sl-dot.active{background:var(--gold);transform:scale(1.4)}

/* counter badge */
.sl-count{position:absolute;top:12px;right:14px;z-index:10;
  background:rgba(0,0,0,.55);backdrop-filter:blur(6px);
  color:#fff;font-size:.7rem;padding:4px 9px;border-radius:2px}

/* thumbnail strip */
.thumb-strip{
  display:flex;gap:6px;padding:10px 20px;
  overflow-x:auto;background:rgba(0,0,0,.35);flex-shrink:0;
  scrollbar-width:thin;scrollbar-color:var(--gold-dim) transparent;
}
.thumb-strip::-webkit-scrollbar{height:3px}
.thumb-strip::-webkit-scrollbar-thumb{background:var(--gold-dim)}
.thumb{width:70px;height:46px;flex-shrink:0;border-radius:3px;overflow:hidden;
  border:1.5px solid transparent;cursor:pointer;opacity:.55;
  transition:border-color .2s,opacity .2s;
}
.thumb:hover,.thumb.active{border-color:var(--gold);opacity:1}
.thumb img{width:100%;height:100%;object-fit:cover;display:block}

/* close */
.m-close{position:absolute;top:13px;left:14px;width:34px;height:34px;border-radius:50%;
  background:rgba(0,0,0,.55);border:1px solid rgba(255,255,255,.2);
  color:#fff;font-size:1rem;cursor:pointer;
  display:flex;align-items:center;justify-content:center;transition:background .2s;z-index:20}
.m-close:hover{background:var(--gold);color:var(--ink);border-color:var(--gold)}

/* modal body */
.m-body{padding:26px 30px 34px}
.m-tag{display:inline-block;font-size:.6rem;letter-spacing:.14em;text-transform:uppercase;
  color:var(--gold);border:1px solid var(--gold-dim);padding:4px 10px;border-radius:2px;margin-bottom:11px}
.m-title{font-family:'Cormorant Garamond',serif;font-size:1.75rem;font-weight:500;color:var(--white);line-height:1.25;margin-bottom:9px}
.m-loc{display:flex;align-items:center;gap:6px;font-size:.79rem;color:var(--muted);margin-bottom:22px}
.m-loc svg{width:13px;height:13px}

/* stats */
.m-stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:1px;
  background:var(--border);border:1px solid var(--border);border-radius:4px;overflow:hidden;margin-bottom:22px}
.stat{background:var(--card);padding:15px 17px;display:flex;flex-direction:column;gap:4px}
.stat-l{font-size:.57rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.stat-v{font-family:'Cormorant Garamond',serif;font-size:1.2rem;font-weight:500;color:var(--white)}

/* features */
.m-feats-title{font-size:.6rem;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin-bottom:10px}
.feat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:7px;margin-bottom:22px}
.feat-row{display:flex;justify-content:space-between;align-items:baseline;gap:8px;
  padding:8px 12px;background:var(--card);border:1px solid var(--border);border-radius:3px;font-size:.76rem}
.feat-row .fl{color:var(--muted)}.feat-row .fv{color:var(--text);font-weight:500;text-align:right}

/* description */
.m-desc{font-size:.82rem;line-height:1.65;color:var(--muted);margin-bottom:22px;padding:14px 16px;
  background:var(--card);border:1px solid var(--border);border-radius:4px}

/* price */
.m-price-bar{display:flex;align-items:baseline;gap:14px;padding:20px 0;
  border-top:1px solid var(--border);border-bottom:1px solid var(--border);margin-bottom:20px}
.m-price-lbl{font-size:.67rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.m-price-val{font-family:'Cormorant Garamond',serif;font-size:2.3rem;font-weight:600;color:var(--gold-l);line-height:1}

/* agent */
.m-agent{display:flex;align-items:center;gap:14px;background:var(--card);border:1px solid var(--border);
  border-radius:4px;padding:15px 17px;margin-bottom:20px}
.m-agent img{width:44px;height:44px;border-radius:50%;border:1px solid var(--gold);object-fit:cover;flex-shrink:0}
.m-agent .aname{font-size:.88rem;font-weight:500;color:var(--white)}
.m-agent .aoff{font-size:.67rem;color:var(--gold);margin-top:2px;letter-spacing:.07em}

/* actions */
.m-actions{display:flex;gap:10px}
.btn{flex:1;padding:13px;border-radius:3px;font-family:'DM Sans',sans-serif;font-size:.72rem;
  font-weight:500;letter-spacing:.1em;text-transform:uppercase;cursor:pointer;
  text-decoration:none;display:flex;align-items:center;justify-content:center;gap:7px;
  transition:all .2s;border:1px solid transparent}
.btn-gold{background:var(--gold);color:var(--ink);border-color:var(--gold)}
.btn-gold:hover{background:var(--gold-l);border-color:var(--gold-l)}
.btn-out{background:transparent;color:var(--text);border-color:var(--border)}
.btn-out:hover{border-color:var(--gold);color:var(--gold)}

/* no-img */
.no-img-slide{width:100%;height:260px;display:flex;align-items:center;justify-content:center;
  background:var(--card);color:var(--muted);font-size:.8rem}

/* responsive */
@media(max-width:640px){
  header{padding:26px 16px 22px}main{padding:26px 16px}
  .hdr-right{display:none}.m-body{padding:18px 16px 28px}
  .m-title{font-size:1.3rem}.m-price-val{font-size:1.8rem}
  .m-actions{flex-direction:column}
  .sl-btn{display:none}
}
</style>
</head>
<body>

<header>
  <img class="hdr-avatar" id="hdr-img" src="" alt="">
  <div class="hdr-info">
    <h1 id="hdr-name">CB ÇİZGİ İlanları</h1>
    <p id="hdr-sub">Coldwell Banker · Gayrimenkul</p>
  </div>
  <div class="hdr-right">
    <div class="n" id="hdr-n">0</div>
    <div class="l">Aktif İlan</div>
  </div>
</header>

<main>
  <div class="sec-lbl">Tüm İlanlar</div>
  <div class="grid" id="grid"></div>
</main>

<!-- MODAL -->
<div class="overlay" id="overlay" onclick="overlayClick(event)">
  <div class="modal" id="modal">
    <button class="m-close" onclick="closeModal()" title="Kapat">✕</button>

    <!-- SLIDER -->
    <div class="slider-wrap" id="sl-wrap">
      <div class="slider-track" id="sl-track"
           onmousedown="swipeStart(event)" ontouchstart="swipeStart(event)"
           onmousemove="swipeMove(event)" ontouchmove="swipeMove(event)"
           onmouseup="swipeEnd(event)"   ontouchend="swipeEnd(event)"
           onmouseleave="swipeCancel()">
        <!-- slides injected by JS -->
      </div>
      <button class="sl-btn sl-prev" onclick="slideBy(-1)">&#8592;</button>
      <button class="sl-btn sl-next" onclick="slideBy(1)">&#8594;</button>
      <div class="sl-dots" id="sl-dots"></div>
      <div class="sl-count" id="sl-count">1 / 1</div>
    </div>

    <!-- THUMBNAIL STRIP -->
    <div class="thumb-strip" id="thumb-strip"></div>

    <!-- BODY -->
    <div class="m-body">
      <div class="m-tag"  id="m-tag"></div>
      <h2 class="m-title" id="m-title"></h2>
      <div class="m-loc">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
        <span id="m-loc"></span>
      </div>
      <div class="m-stats"     id="m-stats"></div>
      <div id="m-feats-wrap"></div>
      <div class="m-desc"      id="m-desc" style="display:none"></div>
      <div class="m-price-bar">
        <span class="m-price-lbl">Satış Fiyatı</span>
        <span class="m-price-val" id="m-price">—</span>
      </div>
      <div class="m-agent"    id="m-agent"></div>
      <div class="m-actions">
        <a class="btn btn-gold" id="m-link" href="#" target="_blank" rel="noopener">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
            <polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>
          </svg>
          İlana Git
        </a>
        <button class="btn btn-out" onclick="closeModal()">Kapat</button>
      </div>
    </div>
  </div>
</div>

<script>
/* ─── DATA ─── */
const LISTINGS = __LISTINGS_JSON__;

/* ─── UTILS ─── */
const $ = id => document.getElementById(id);
const esc = s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

/* ─── HEADER ─── */
(function initHeader(){
  const agents  = [...new Set(LISTINGS.map(l=>l.agent_name).filter(Boolean))];
  const imgs    = [...new Set(LISTINGS.map(l=>l.agent_img ).filter(Boolean))];
  const offices = [...new Set(LISTINGS.map(l=>l.agent_office).filter(Boolean))];
  const hdrImg = $('hdr-img');
  if(imgs[0]){ hdrImg.src=imgs[0]; hdrImg.alt=agents[0]||''; }
  else hdrImg.style.display='none';
  if(agents[0])  $('hdr-name').textContent = agents[0];
  if(offices[0]) $('hdr-sub').textContent  = offices[0]+' · Coldwell Banker';
  $('hdr-n').textContent = LISTINGS.length;
})();

/* ─── GRID ─── */
function buildMeta(l){
  const r=[];
  if(l.rooms)       r.push({label:'Oda',    value:l.rooms});
  if(l.sqm)         r.push({label:'Alan',   value:l.sqm});
  if(l.status)      r.push({label:'Durum',  value:l.status});
  if(l.type)        r.push({label:'Tür',    value:l.type});
  if(l.portfolio_no)r.push({label:'No',     value:'#'+l.portfolio_no});
  for(const f of(l.features||[])){
    if(!r.find(x=>x.label===f.label)) r.push(f);
    if(r.length>=6) break;
  }
  return r;
}

(function renderGrid(){
  $('grid').innerHTML = LISTINGS.map((l,i)=>{
    const img0 = (l.images||[])[0]||'';
    const pc   = (l.images||[]).length||1;
    const meta = buildMeta(l).slice(0,2).map(m=>
      `<div class="mi"><span class="mi-l">${esc(m.label)}</span><span class="mi-v">${esc(m.value)}</span></div>`
    ).join('');
    return `
<article class="card" onclick="openModal(${i})">
  <div class="card-img">
    ${img0
      ?`<img src="${esc(img0)}" alt="${esc(l.title)}" loading="lazy">`
      :`<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:var(--card);color:var(--muted);font-size:.75rem">Görsel yok</div>`}
    <div class="badge">${esc(l.status||'Satılık')}</div>
    <div class="photo-ct">
      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <rect x="3" y="3" width="18" height="18" rx="2"/>
        <circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>
      </svg>${pc}
    </div>
  </div>
  <div class="card-body">
    <div class="card-kind">${esc(l.type||'İlan')}</div>
    <h2 class="card-title">${esc(l.title)}</h2>
    <div class="card-loc">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>
      </svg>
      ${esc(l.location||'—')}
    </div>
    <div class="card-meta">${meta}</div>
    <div class="card-foot">
      <div>
        <div class="price">${esc(l.price||'Fiyat sorunuz')}</div>
        <div class="pno">Portföy #${esc(l.portfolio_no)}</div>
      </div>
      <div class="arr">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>
        </svg>
      </div>
    </div>
  </div>
</article>`;
  }).join('');
})();

/* ─── SLIDER STATE ─── */
let _slImages = [], _slIdx = 0;
let _swipeX0 = null, _swipeActive = false;

function buildSlider(images){
  _slImages = images.length ? images : [''];
  _slIdx = 0;
  const track = $('sl-track');
  const dots  = $('sl-dots');
  const thumbs = $('thumb-strip');

  track.innerHTML = _slImages.map((src,i)=>
    src
      ?`<div class="slide"><img src="${esc(src)}" alt="Fotoğraf ${i+1}" loading="${i===0?'eager':'lazy'}"><div class="slide-overlay"></div></div>`
      :`<div class="slide"><div class="no-img-slide">Görsel mevcut değil</div></div>`
  ).join('');

  dots.innerHTML = _slImages.length>1
    ? _slImages.map((_,i)=>`<button class="sl-dot${i===0?' active':''}" onclick="goSlide(${i})"></button>`).join('')
    : '';

  thumbs.innerHTML = _slImages.length>1
    ? _slImages.map((src,i)=>
        `<div class="thumb${i===0?' active':''}" onclick="goSlide(${i})">
           <img src="${esc(src)}" alt="" loading="lazy">
         </div>`
      ).join('')
    : '';

  updateSlider();
}

function updateSlider(){
  const n = _slImages.length;
  $('sl-track').style.transform = `translateX(-${_slIdx*100}%)`;
  $('sl-track').style.transition = 'transform .4s cubic-bezier(.22,.61,.36,1)';
  $('sl-count').textContent = `${_slIdx+1} / ${n}`;
  // dots
  document.querySelectorAll('.sl-dot').forEach((d,i)=> d.classList.toggle('active',i===_slIdx));
  // thumbs
  const thumbEls = $('thumb-strip').querySelectorAll('.thumb');
  thumbEls.forEach((t,i)=>{
    t.classList.toggle('active',i===_slIdx);
  });
  // scroll active thumb into view
  if(thumbEls[_slIdx]) thumbEls[_slIdx].scrollIntoView({behavior:'smooth',inline:'nearest',block:'nearest'});
  // hide arrows if single image
  document.querySelectorAll('.sl-btn').forEach(b=> b.style.display=n>1?'':'none');
}

function goSlide(idx){
  _slIdx = (idx + _slImages.length) % _slImages.length;
  updateSlider();
}

function slideBy(dir){ goSlide(_slIdx + dir); }

/* touch / mouse swipe */
function swipeStart(e){
  _swipeX0 = (e.touches?e.touches[0]:e).clientX;
  _swipeActive = true;
}
function swipeMove(e){
  if(!_swipeActive) return;
  const dx = (e.touches?e.touches[0]:e).clientX - _swipeX0;
  $('sl-track').style.transition='none';
  $('sl-track').style.transform=`translateX(calc(-${_slIdx*100}% + ${dx}px))`;
}
function swipeEnd(e){
  if(!_swipeActive) return;
  _swipeActive=false;
  const dx=(e.changedTouches?e.changedTouches[0]:e).clientX-_swipeX0;
  if(Math.abs(dx)>50) slideBy(dx<0?1:-1);
  else updateSlider();
}
function swipeCancel(){ if(_swipeActive){_swipeActive=false;updateSlider();} }

/* keyboard */
document.addEventListener('keydown',e=>{
  if(!$('overlay').classList.contains('open')) return;
  if(e.key==='ArrowLeft')  slideBy(-1);
  if(e.key==='ArrowRight') slideBy(1);
  if(e.key==='Escape')     closeModal();
});

/* ─── MODAL OPEN ─── */
function openModal(idx){
  const l = LISTINGS[idx];
  // slider
  buildSlider(l.images||[]);
  // meta
  $('m-tag').textContent   = (l.type||'İlan')+' · '+(l.status||'Satılık');
  $('m-title').textContent = l.title;
  $('m-loc').textContent   = l.location||'—';
  $('m-price').textContent = l.price||'Fiyat sorunuz';
  $('m-link').href         = l.url;

  // stats
  const meta = buildMeta(l).slice(0,6);
  $('m-stats').innerHTML = meta.map(m=>
    `<div class="stat"><span class="stat-l">${esc(m.label)}</span><span class="stat-v">${esc(m.value)}</span></div>`
  ).join('');

  // features (exclude what's already in stats)
  const usedLabels = new Set(meta.map(m=>m.label));
  const feats = (l.features||[]).filter(f=>!usedLabels.has(f.label));
  $('m-feats-wrap').innerHTML = feats.length
    ?`<div class="m-feats-title">Özellikler</div>
      <div class="feat-grid">
        ${feats.map(f=>`<div class="feat-row"><span class="fl">${esc(f.label)}</span><span class="fv">${esc(f.value)}</span></div>`).join('')}
      </div>`
    :'';

  // description
  const descEl = $('m-desc');
  if(l.description){ descEl.textContent=l.description; descEl.style.display=''; }
  else descEl.style.display='none';

  // agent
  $('m-agent').innerHTML=`
    ${l.agent_img
      ?`<img src="${esc(l.agent_img)}" alt="${esc(l.agent_name||'')}">`
      :`<div style="width:44px;height:44px;border-radius:50%;background:var(--gold-dim);border:1px solid var(--gold);
           display:flex;align-items:center;justify-content:center;font-family:'Cormorant Garamond',serif;color:var(--gold);font-size:.9rem;flex-shrink:0">
           ${esc((l.agent_name||'CB')[0])}</div>`}
    <div><div class="aname">${esc(l.agent_name||'Danışman')}</div>
         <div class="aoff">${esc(l.agent_office||'Coldwell Banker')}</div></div>`;

  $('overlay').classList.add('open');
  document.body.style.overflow='hidden';
  $('modal').scrollTop=0;
}

function closeModal(){
  $('overlay').classList.remove('open');
  document.body.style.overflow='';
}
function overlayClick(e){ if(e.target===$('overlay')) closeModal(); }
</script>
</body>
</html>
"""


# ─── HTML BUILDER ─────────────────────────────────────────────────────────────

def build_html(listings: list[dict]) -> str:
    payload = json.dumps(listings, ensure_ascii=False)
    return HTML.replace("__LISTINGS_JSON__", payload)


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    listings = scrape_listings()
    if not listings:
        print("Hiç ilan bulunamadı.")
        sys.exit(1)

    html = build_html(listings)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n{'='*60}")
    print(f"  ✅  {len(listings)} ilan  →  {OUTPUT_FILE}")
    print(f"{'='*60}")
    print("  Tarayıcıda açmak için:")
    print(f"    python3 -m http.server 8080")
    print(f"    → http://localhost:8080/{OUTPUT_FILE}")
    print("  veya dosyayı doğrudan çift tıklayın.")
    print()


if __name__ == "__main__":
    main()
