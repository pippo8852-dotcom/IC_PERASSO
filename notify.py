"""Avvisi Telegram per IC Perasso: novità del sito, Albo online, PN Scuola e Competenze.
   (Le comunicazioni UST Milano sono gestite separatamente su Make.com, perché il sito
   del Ministero blocca le richieste provenienti dai server di GitHub Actions.)"""
import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

TOKEN = os.environ["8255694064:AAEvMIstt4UKrvC_XibNI3DD4HycMPez5SA"]
CHAT_ID = os.environ["-1002972103296"]      # es. @nomecanale oppure -100123...
STATO = Path("stato.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (avvisi scuola)"}

NUVOLA = "https://nuvola.madisoft.it/bacheca-digitale/bacheca"
# (nome, tipo, indirizzo)
FONTI = [
    ("📰 Novità", "feed", "https://icperasso.edu.it/feed/"),
    ("📌 Albo online", "nuvola", f"{NUVOLA}/MIIC8GH008/1/IN_PUBBLICAZIONE/0/show"),
    ("📌 PN Scuola e Competenze", "nuvola", f"{NUVOLA}/ricerca/MIIC8GH008/8/show"),
    ("📋 Circolari", "nuvola", f"{NUVOLA}/ricerca/MIIC8GH008/5/show"),
]

RIGA = re.compile(r"<tr[^>]*data-entity-id=.*?</tr>", re.S)
CAMPI = re.compile(
    r'data-entity-id="(?P<id>\d+)".*?'
    r'href="(?P<link>https://nuvola\.madisoft\.it/bacheca-digitale/\d+/documento/[^"]+)".*?'
    r"oggetto[^>]*>(?P<oggetto>[^<]+)</td>.*?"
    r"numeroRegistro[^>]*>(?P<numero>[^<]+)</td>.*?"
    r"dataPubblicazioneOnline[^>]*>(?P<data>[^<]+)</td>",
    re.S,
)


def leggi_feed(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    voci = []
    for item in ET.fromstring(r.content).iter("item"):
        link = item.findtext("link")
        titolo = html.unescape(item.findtext("title", ""))
        voci.append((link, f"{titolo}\n{link}"))
    return voci


def leggi_nuvola(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    voci = []
    for riga in RIGA.findall(r.text):
        m = CAMPI.search(riga)
        if m:
            d = {k: html.unescape(v.strip()) for k, v in m.groupdict().items()}
            voci.append((
                d["id"],
                f"{d['oggetto']}\nN. {d['numero']} del {d['data']}\n{d['link']}",
            ))
    return voci


def leggi(tipo, url):
    return leggi_feed(url) if tipo == "feed" else leggi_nuvola(url)


def invia(testo):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": testo, "disable_web_page_preview": "true"},
        timeout=20,
    )
    r.raise_for_status()


def main():
    stato = json.loads(STATO.read_text()) if STATO.exists() else {}

    for nome, tipo, url in FONTI:
        try:
            voci = leggi(tipo, url)
        except Exception as e:
            print(f"[{nome}] errore: {e}")
            continue
        if not voci:
            print(f"[{nome}] nessun elemento letto: controlla se il layout è cambiato")
            continue

        visti = set(stato.get(nome, {}).get("visti", []))
        primo_giro = nome not in stato          # al primo giro non invia nulla
        nuove = [v for v in voci if v[0] not in visti]

        if not primo_giro:
            for chiave, testo in reversed(nuove):   # dalla più vecchia alla più recente
                invia(f"{nome}\n{testo}")
                time.sleep(1)

        stato[nome] = {"visti": sorted(visti | {v[0] for v in voci})}
        print(f"[{nome}] letti {len(voci)}, inviati {0 if primo_giro else len(nuove)}")

    STATO.write_text(json.dumps(stato, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
