import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# Configurazione Telegram (legge le variabili impostate nei Secrets del repository)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# File di stato per memorizzare gli ID degli articoli già inviati
STATE_FILE = "sent_items.json"

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
        print("Token o Chat ID di Telegram mancanti nei Secrets.")
        return
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
    except Exception as e:
        print(f"Errore nell'invio del messaggio Telegram: {e}")

# --- Funzioni di scraping ---

def fetch_novita():
    items = []
    url = "https://icperasso.edu.it/feed/"
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.content, "xml")
            for item in soup.find_all("item"):
                title = item.title.text.strip() if item.title else "Senza titolo"
                link = item.link.text.strip() if item.link else ""
                guid = item.guid.text.strip() if item.guid else link
                pub_date = item.pubDate.text.strip() if item.pubDate else ""
                items.append({"id": guid, "title": title, "link": link, "date": pub_date})
    except Exception as e:
        print(f"Errore lettura Novità: {e}")
    return items

def fetch_nuvola_bacheca(category_id, name_prefix):
    items = []
    # Endpoint di ricerca per recuperare l'elenco della bacheca Nuvola
    url = f"https://nuvola.madisoft.it/bacheca-digitale/ricerca/MIIC8GH008/{category_id}/show"
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Cerca i link alle singole comunicazioni Nuvola
            for a in soup.find_all('a', href=True):
                href = a['href']
                if f"/bacheca-digitale/bacheca/MIIC8GH008/{category_id}/" in href:
                    item_id = href.split("?")[0]
                    title = a.get_text(strip=True)
                    if title and len(title) > 5:
                        full_link = href if href.startswith("http") else f"https://nuvola.madisoft.it{href}"
                        items.append({"id": item_id, "title": title, "link": full_link, "date": ""})
                        
    except Exception as e:
        print(f"Errore lettura {name_prefix}: {e}")
    
    # Rimuove eventuali duplicati mantenendo l'ordine
    unique_items = {}
    for item in items:
        if item["id"] not in unique_items:
            unique_items[item["id"]] = item
            
    return list(unique_items.values())

def main():
    state = load_state()
    sources = {
        "novita": {"name": "📰 Novità", "fetch": fetch_novita},
        "albo": {"name": "📌 Albo online", "fetch": lambda: fetch_nuvola_bacheca("1", "Albo online")},
        "pn": {"name": "📌 PN Scuola e Competenze", "fetch": lambda: fetch_nuvola_bacheca("4", "PN Scuola")},
        "circolari": {"name": "📢 Circolari", "fetch": lambda: fetch_nuvola_bacheca("5", "Circolari")}
    }

    for key, src in sources.items():
        if key not in state:
            state[key] = []
        
        known_ids = set(state[key])
        items = src["fetch"]()
        
        new_items = [item for item in items if item["id"] not in known_ids]
        
        print(f"[{src['name']}] letti {len(items)}, inviati {len(new_items)}")

        # Se è il primo avvio in assoluto per questa fonte, salva la lista senza inviare notifiche (evita notifiche massive)
        is_first_run = len(known_ids) == 0 and len(state[key]) == 0

        for item in reversed(new_items):
            if not is_first_run:
                msg = f"<b>{src['name']}</b>\n\n<a href='{item['link']}'>{item['title']}</a>"
                send_telegram_message(msg)
            state[key].append(item["id"])

    save_state(state)

if __name__ == "__main__":
    main()
