import os
import json
import re
import html
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET
import requests
from bs4 import BeautifulSoup

# Configurazione Telegram
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

STATE_FILE = "sent_items.json"
SEND_DELAY = 3           # secondi di pausa tra due messaggi (Telegram: circa 20 al minuto per chat)
MAX_SEND_ATTEMPTS = 3    # tentativi per ogni messaggio quando Telegram risponde 429
MAX_RETRY_WAIT = 60      # attesa massima accettata per un nuovo tentativo, in secondi
FEED_URL = "https://icperasso.edu.it/feed/"
# Indirizzi alternativi dello stesso feed WordPress, provati in ordine se il primo non risponde.
FEED_URLS = [
    FEED_URL,
    "https://icperasso.edu.it/?feed=rss2",
    "https://icperasso.edu.it/feed/atom/",
]
# Alternativa più leggera del feed: l'API di WordPress (stesso sito, stessi articoli).
SITE_URL = "https://icperasso.edu.it"
WP_API_URL = f"{SITE_URL}/wp-json/wp/v2/posts?per_page=10&_fields=id,link,title,date_gmt"
FEED_TIMEOUT = 20        # secondi di attesa massima per ogni richiesta
FEED_ATTEMPTS = 2        # tentativi per ogni indirizzo
FEED_RETRY_WAIT = 5      # secondi di attesa tra due tentativi
FEED_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
}
ATOM_NS = "{http://www.w3.org/2005/Atom}"

LOCAL_TZ_NAME = "Europe/Rome"
DATE_RE = re.compile(
    r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b(?:[^\d]{0,12}?(\d{1,2})[:.](\d{2})\b)?"
)

# Cella che contiene SOLO una data (e magari l'ora): gg/mm/aaaa, gg-mm-aaaa o gg.mm.aaaa
DATE_ONLY_RE = re.compile(
    r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})(?:\s*(?:ore|alle|-|,)?\s*(\d{1,2})[:.](\d{2}))?"
)

def format_local(dt):
    """Formatta una data come gg/mm/aaaa HH:MM, convertita all'ora italiana se ha il fuso."""
    if dt.tzinfo is not None:
        try:
            dt = dt.astimezone(ZoneInfo(LOCAL_TZ_NAME))
        except Exception:
            pass
    return dt.strftime("%d/%m/%Y %H:%M")

def parse_feed_date(text):
    """Legge la data di un feed (RSS: RFC 822, Atom: ISO 8601). None se assente o illeggibile."""
    if not text or not text.strip():
        return None
    text = text.strip()
    try:
        return format_local(parsedate_to_datetime(text))
    except (TypeError, ValueError):
        pass
    try:
        return format_local(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None

def parse_wp_gmt_date(text):
    """Data 'date_gmt' dell'API WordPress (ISO senza fuso, in UTC). None se assente o illeggibile."""
    if not text or not str(text).strip():
        return None
    try:
        dt = datetime.fromisoformat(str(text).strip())
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return format_local(dt)

def extract_date_from_text(text, whole=False):
    """Legge una data gg/mm/aaaa (con orario facoltativo).
    whole=False: cerca la prima data ovunque nel testo.
    whole=True: accetta il testo solo se è interamente una data (per riconoscere una colonna
    "data" senza confonderla con date citate dentro un titolo). None se non trova nulla."""
    if whole:
        match = DATE_ONLY_RE.fullmatch((text or "").strip())
    else:
        match = DATE_RE.search(text or "")
    if not match:
        return None
    day, month, year, hour, minute = match.groups()
    try:
        datetime(int(year), int(month), int(day))
    except ValueError:
        return None
    result = f"{int(day):02d}/{int(month):02d}/{year}"
    if hour is not None and int(hour) < 24 and int(minute) < 60:
        result += f" {int(hour):02d}:{minute}"
    return result

def extract_row_date(row):
    """Data di pubblicazione di una riga di tabella: la prima cella che contiene solo una data."""
    for cell in row.find_all(["td", "th"]):
        date = extract_date_from_text(cell.get_text(" ", strip=True), whole=True)
        if date:
            return date
    return None

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Errore nel salvataggio dello stato: {e}")

def _retry_after(resp):
    """Secondi di attesa richiesti da Telegram dopo un 429 (None se non indicati)."""
    try:
        value = resp.json().get("parameters", {}).get("retry_after")
        if value is not None:
            return float(value)
    except Exception:
        pass
    try:
        return float(resp.headers.get("Retry-After"))
    except (TypeError, ValueError):
        return None

def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Token o Chat ID mancanti nei Secrets.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 429 and attempt < MAX_SEND_ATTEMPTS:
                wait = (_retry_after(response) or 5) + 1
                if wait <= MAX_RETRY_WAIT:
                    print(f"⏳ Telegram chiede di rallentare (429): attendo {wait:.0f}s e riprovo "
                          f"({attempt}/{MAX_SEND_ATTEMPTS})")
                    time.sleep(wait)
                    continue
            response.raise_for_status()
            print("✅ Messaggio Telegram inviato!")
            return True
        except Exception as e:
            print(f"❌ Errore invio Telegram: {e}")
            return False
    return False

def fetch_pn():
    items = []
    category_id = "8"
    url = f"https://nuvola.madisoft.it/bacheca-digitale/bacheca/ricerca/MIIC8GH008/{category_id}/show"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        print(f"📡 Status HTTP Nuvola: {resp.status_code}")
        
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Scansioniamo tutte le righe delle tabelle presenti nella bacheca
            rows = soup.find_all('tr')
            print(f"🔍 Righe tabella trovate nella pagina: {len(rows)}")
            for sample in rows[:3]:
                cells = [c.get_text(" ", strip=True)[:60] for c in sample.find_all(["td", "th"])]
                print(f"🧪 Celle riga di esempio ({len(cells)}): {cells!r}")
            
            for row in rows:
                links = row.find_all('a', href=True)
                for a in links:
                    href = a['href']
                    
                    # Cerchiamo l'ID numerico dell'atto nel percorso dell'URL
                    match = re.search(r'/bacheca-digitale/(\d+)', href)
                    if match:
                        doc_id = match.group(1)
                        title = row.get_text(" ", strip=True)
                        
                        # Pulizia del testo per tenere solo il titolo dell'oggetto dell'atto
                        lines = [line.strip() for line in title.splitlines() if line.strip()]
                        clean_title = " ".join(lines)
                        
                        # Tronchiamo l'eventuale prefisso generico se troppo lungo
                        if len(clean_title) > 200:
                            clean_title = clean_title[:197] + "..."
                            
                        direct_link = f"https://nuvola.madisoft.it/bacheca-digitale/{doc_id}/documento/MIIC8GH008"
                        if len(items) < 2:
                            print(f"🧪 HTML riga atto {doc_id}: {str(row)[:1500]!r}")
                        items.append({"id": doc_id, "title": clean_title, "link": direct_link,
                                      "date": extract_row_date(row)})
                        break # Un solo ID valido per riga

            # Fallback nel caso la struttura non usi tag <tr>
            if not items:
                all_links = soup.find_all('a', href=True)
                for a in all_links:
                    href = a['href']
                    match = re.search(r'/bacheca-digitale/(\d+)', href)
                    if match:
                        doc_id = match.group(1)
                        title = a.get_text(strip=True) or f"Comunicazione PN {doc_id}"
                        direct_link = f"https://nuvola.madisoft.it/bacheca-digitale/{doc_id}/documento/MIIC8GH008"
                        items.append({"id": doc_id, "title": title, "link": direct_link, "date": None})

    except Exception as e:
        print(f"❌ Errore durante la lettura della bacheca PN: {e}")
            
    # Rimozione duplicati mantenendo l'ordine
    unique_items = {}
    for item in items:
        if item["id"] not in unique_items:
            unique_items[item["id"]] = item
            
    print(f"📋 Atti unici identificati: {len(unique_items)}")
    return list(unique_items.values())

def _child(entry, *tags):
    """Primo figlio dell'elemento con uno dei tag indicati (None se assente)."""
    for tag in tags:
        node = entry.find(tag)
        if node is not None:
            return node
    return None

def _entry_link(entry):
    """Link di una voce RSS (<link>testo</link>) o Atom (<link href=... />)."""
    for node in entry.findall("link") + entry.findall(f"{ATOM_NS}link"):
        if node.get("href") and node.get("rel", "alternate") == "alternate":
            return node.get("href").strip()
        if node.text and node.text.strip():
            return node.text.strip()
    return None

def _fetch_with_retries(url, parse):
    """Scarica un indirizzo e ne interpreta la risposta con parse(resp), con qualche nuovo
    tentativo. Restituisce la lista di notizie oppure None se non riesce."""
    for attempt in range(1, FEED_ATTEMPTS + 1):
        try:
            resp = requests.get(url, headers=FEED_HEADERS, timeout=FEED_TIMEOUT)
            print(f"📡 Status HTTP feed ({url}): {resp.status_code}")
            resp.raise_for_status()
            return parse(resp)
        except Exception as e:
            print(f"❌ Errore durante la lettura del feed (tentativo {attempt}/{FEED_ATTEMPTS}): {e}")
            if attempt < FEED_ATTEMPTS:
                time.sleep(FEED_RETRY_WAIT)
    return None

def _clean_title(text):
    title = " ".join((text or "").split())
    if not title:
        return "Nuova notizia"
    return title[:197] + "..." if len(title) > 200 else title

def _parse_feed_xml(resp):
    """Notizie di un feed RSS 2.0 o Atom."""
    root = ET.fromstring(resp.content)
    entries = root.findall(".//item") or root.findall(f".//{ATOM_NS}entry")
    unique_items = {}
    for entry in entries:
        link = _entry_link(entry)
        if not link:
            continue

        id_node = _child(entry, "guid", f"{ATOM_NS}id")
        item_id = (id_node.text or "").strip() if id_node is not None else ""
        item_id = item_id or link

        title_node = _child(entry, "title", f"{ATOM_NS}title")
        title = _clean_title(title_node.text if title_node is not None else "")

        date_node = _child(entry, "pubDate", f"{ATOM_NS}published", f"{ATOM_NS}updated")
        date = parse_feed_date(date_node.text) if date_node is not None else None
        if not unique_items:
            raw = date_node.text if date_node is not None else None
            print(f"🧪 Prima voce del feed: data grezza {raw!r} -> {date!r}")

        if item_id not in unique_items:
            unique_items[item_id] = {"id": item_id, "title": title, "link": link, "date": date}

    print(f"📋 Notizie uniche nel feed: {len(unique_items)}")
    return list(unique_items.values())

def _parse_wp_posts(resp):
    """Notizie dall'API REST di WordPress. Gli ID hanno lo stesso formato dei guid del feed
    (https://sito/?p=123), così non si creano duplicati passando da una fonte all'altra."""
    posts = resp.json()
    if not isinstance(posts, list):
        raise ValueError(f"risposta API inattesa: {str(posts)[:100]}")
    unique_items = {}
    for post in posts:
        post_id, link = post.get("id"), post.get("link")
        if not post_id or not link:
            continue
        raw_title = (post.get("title") or {}).get("rendered", "")
        title = _clean_title(html.unescape(re.sub(r"<[^>]+>", "", raw_title)))
        item_id = f"{SITE_URL}/?p={post_id}"
        if item_id not in unique_items:
            unique_items[item_id] = {"id": item_id, "title": title, "link": link,
                                     "date": parse_wp_gmt_date(post.get("date_gmt"))}
    print(f"📋 Notizie uniche dall'API WordPress: {len(unique_items)}")
    return list(unique_items.values())

def fetch_novita():
    """Prova in ordine: feed principale, API WordPress, poi gli altri indirizzi del feed."""
    attempts = [(FEED_URLS[0], _parse_feed_xml), (WP_API_URL, _parse_wp_posts)]
    attempts += [(url, _parse_feed_xml) for url in FEED_URLS[1:]]
    for url, parse in attempts:
        items = _fetch_with_retries(url, parse)
        if items is not None:
            return items
    return []

# Ogni fonte ha: chiave nello stato, etichetta del messaggio, funzione di lettura
# e "enabled" (se False la fonte viene saltata del tutto: nessuna richiesta, nessun invio).
# La funzione è chiamata tramite lambda per essere risolta a ogni esecuzione.
SOURCES = [
    {"key": "bacheca_8", "label": "📌 PN / Comunicazioni IC Perasso",
     "fetch": lambda: fetch_pn(), "enabled": True},
    # Novità del sito: per disattivarle basta mettere "enabled": False
    {"key": "feed_icperasso", "label": "📰 News IC Perasso",
     "fetch": lambda: fetch_novita(), "enabled": True},
]

def process_source(state, source):
    key = source["key"]
    state.setdefault(key, [])
    known_ids = set(state[key])
    items = source["fetch"]()

    new_items = [item for item in items if item["id"] not in known_ids]
    print(f"📊 Summary [{source['label']}]: Letti {len(items)} | Nuovi da inviare {len(new_items)}")
    print(f"🗓 Date trovate: {sum(1 for i in items if i.get('date'))} su {len(items)}")

    for n, item in enumerate(reversed(new_items)):
        if n > 0:
            time.sleep(SEND_DELAY)  # rispetta il limite di Telegram sui messaggi al minuto
        # Titolo e link vanno "escapati": con parse_mode HTML un carattere come & o <
        # farebbe rifiutare il messaggio da Telegram.
        title = html.escape(item["title"], quote=False)
        link = html.escape(item["link"], quote=True)
        date_line = f"🗓 {html.escape(item['date'])}\n" if item.get("date") else ""
        msg = f"<b>{source['label']}</b>\n{date_line}\n<a href='{link}'>{title}</a>"
        if send_telegram_message(msg):
            state[key].append(item["id"])
        else:
            # Invio fallito: non segniamo l'atto come inviato, così verrà
            # ritentato alla prossima esecuzione. Ci fermiamo per mantenere l'ordine.
            print(f"⚠️ Invio fallito per l'atto {item['id']}: riproverò alla prossima esecuzione.")
            break

def main():
    state = load_state()
    for source in SOURCES:
        if not source.get("enabled", True):
            continue
        process_source(state, source)
    save_state(state)

if __name__ == "__main__":
    main()
