import os
import json
import re
import html
import xml.etree.ElementTree as ET
import requests
from bs4 import BeautifulSoup

# Configurazione Telegram
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

STATE_FILE = "sent_items.json"
FEED_URL = "https://icperasso.edu.it/feed/"
ATOM_NS = "{http://www.w3.org/2005/Atom}"

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
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        print("✅ Messaggio Telegram inviato!")
        return True
    except Exception as e:
        print(f"❌ Errore invio Telegram: {e}")
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
                        items.append({"id": doc_id, "title": clean_title, "link": direct_link})
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
                        items.append({"id": doc_id, "title": title, "link": direct_link})

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

def fetch_novita(url=FEED_URL):
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        print(f"📡 Status HTTP feed: {resp.status_code}")
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        print(f"❌ Errore durante la lettura del feed: {e}")
        return []

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
        title = " ".join((title_node.text or "").split()) if title_node is not None else ""
        if not title:
            title = "Nuova notizia"
        if len(title) > 200:
            title = title[:197] + "..."

        if item_id not in unique_items:
            unique_items[item_id] = {"id": item_id, "title": title, "link": link}

    print(f"📋 Notizie uniche nel feed: {len(unique_items)}")
    return list(unique_items.values())

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

    for item in reversed(new_items):
        # Titolo e link vanno "escapati": con parse_mode HTML un carattere come & o <
        # farebbe rifiutare il messaggio da Telegram.
        title = html.escape(item["title"], quote=False)
        link = html.escape(item["link"], quote=True)
        msg = f"<b>{source['label']}</b>\n\n<a href='{link}'>{title}</a>"
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
