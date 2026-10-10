import os
import json
import requests
from bs4 import BeautifulSoup

# Configurazione Telegram
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

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
        print("❌ Token o Chat ID mancanti nei Secrets.")
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
        print("✅ Messaggio Telegram inviato!")
    except Exception as e:
        print(f"❌ Errore invio Telegram: {e}")

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
                items.append({"id": guid, "title": title, "link": link})
    except Exception as e:
        print(f"Errore lettura Novità: {e}")
    return items

def fetch_albo():
    items = []
    category_id = "1"
    url = f"https://nuvola.madisoft.it/bacheca-digitale/bacheca/ricerca/MIIC8GH008/{category_id}/show"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all('a', href=True):
                href = a['href']
                
                # Filtriamo ed estraiamo solo gli atti effettivi in pubblicazione
                if "/bacheca-digitale/bacheca/MIIC8GH008/" in href and "/IN_PUBBLICAZIONE/" in href:
                    title = a.get_text(strip=True)
                    if title and len(title) > 5 and "torna" not in title.lower() and "oggetto" not in title.lower() and "ricerca" not in title.lower():
                        item_id = href.split("?")[0]
                        full_link = href if href.startswith("http") else f"https://nuvola.madisoft.it{href}"
                        items.append({"id": item_id, "title": title, "link": full_link})
    except Exception as e:
        print(f"Errore lettura Albo online: {e}")
            
    unique_items = {}
    for item in items:
        if item["id"] not in unique_items:
            unique_items[item["id"]] = item
    return list(unique_items.values())

def main():
    state = load_state()
    sources = {
        "novita": {"name": "📰 Novità", "fetch": fetch_novita},
        "albo": {"name": "📌 Albo online", "fetch": fetch_albo}
    }

    for key, src in sources.items():
        if key not in state:
            state[key] = []
        
        known_ids = set(state[key])
        items = src["fetch"]()
        
        new_items = [item for item in items if item["id"] not in known_ids]
        
        print(f"[{src['name']}] Letti {len(items)}, Nuovi da inviare {len(new_items)}")

        for item in reversed(new_items):
            msg = f"<b>{src['name']} IC Perasso</b>\n\n<a href='{item['link']}'>{item['title']}</a>"
            send_telegram_message(msg)
            state[key].append(item["id"])

    save_state(state)

if __name__ == "__main__":
    main()
