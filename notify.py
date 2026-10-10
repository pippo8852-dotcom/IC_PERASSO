import os
import requests
from bs4 import BeautifulSoup

def debug_fetch():
    category_id = "8"
    url = f"https://nuvola.madisoft.it/bacheca-digitale/bacheca/ricerca/MIIC8GH008/{category_id}/show"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        print(f"Status Code: {resp.status_code}")
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            links = soup.find_all('a', href=True)
            print(f"Totale tag <a> trovati: {len(links)}")
            
            for i, a in enumerate(links[:30]): # Stampiamo i primi 30 link per controllo
                print(f"[{i}] href: {a.get('href')} | testo: {a.get_text(strip=True)}")
        else:
            print(f"Errore HTTP: {resp.status_code}")
    except Exception as e:
        print(f"Eccezione durante il debug: {e}")

if __name__ == "__main__":
    debug_fetch()
