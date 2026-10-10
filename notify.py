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

def fetch_pn():
    items = []
    category_id = "8"
    url = f"https://nuvola.madisoft.it/bacheca-digitale/bacheca/ricerca/MIIC8GH008/{category_id}/show"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        print(f"Status code pagina PN: {resp.status_code}")
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all('a', href=True):
                href = a['href']
                
                # Cattura i link sotto /bacheca-digitale/ escludendo le schermate di ricerca e di show generali
                if "/bacheca-digitale/" in href and not href.endswith("/show") and not href.endswith("/show/") and "ricerca" not in href:
                    title = a.get_text(strip=True)
                    if title and len(title) > 4 and "torna" not in title.lower() and "oggetto" not in title.lower() and "ricerca" not in title.lower():
                        item_id = href.split("?")[0]
                        full_link = href if href.startswith("http") else f"https://nuvola.madisoft.it{href}"
                        items.append({"id": item_id, "title": title, "link": full_link})
    except Exception as e:
        print(f"Errore lettura PN: {e}")
            
    unique_items = {}
    for item in items:
        if item["id"] not in unique_items:
            unique_items[item["id"]] = item
    return list(unique_items.values())

def main():
    state = load_state()
    key = "pn"
    
    if key not in state:
        state[key] = []
    
    known_ids = set(state[key])
    items = fetch_pn()
    
    new_items = [item for item in items if item["id"] not in known_ids]
    
    print(f"[📌 PN] Letti {len(items)}, Nuovi da inviare {len(new_items)}")

    for item in reversed(new_items):
        msg = f"<b>📌 PN / Comunicazioni IC Perasso</b>\n\n<a href='{item['link']}'>{item['title']}</a>"
        send_telegram_message(msg)
        state[key].append(item["id"])

    save_state(state)

if __name__ == "__main__":
    main()
