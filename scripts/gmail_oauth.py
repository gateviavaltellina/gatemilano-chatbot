"""Genera il GMAIL_REFRESH_TOKEN per il risponditore email. Da lanciare UNA volta, in locale.

Il bot gira su Railway e non ha un browser: il consenso OAuth va dato da un computer
con un browser, accedendo con l'account Gmail che riceve la posta di info@. Lo script
apre il browser, riceve il codice su un indirizzo locale (127.0.0.1) e lo scambia con
un refresh token, che poi va messo nelle variabili di Railway.

Solo libreria standard: non serve installare nulla.

    python3 gmail_oauth.py client_secret.json

dove client_secret.json è il file scaricato da Google Cloud (client "App desktop").
Alla fine stampa le righe da incollare nel Raw Editor delle variabili di Railway.

In alternativa, senza file: `python3 gmail_oauth.py --client-id XXXX` e il secret
viene chiesto a parte (non resta nella cronologia della shell).
"""
from __future__ import annotations

import argparse
import getpass
import http.server
import json
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser

SCOPE = "https://www.googleapis.com/auth/gmail.modify"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def _wait_for_code(state: str) -> tuple[str, int, http.server.HTTPServer]:
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] != state:
                result["error"] = "state non corrispondente"
            elif "error" in q:
                result["error"] = q["error"][0]
            else:
                result["code"] = q.get("code", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Fatto, puoi chiudere questa scheda." if "code" in result else "Errore: torna al terminale."
            self.wfile.write(f"<p>{msg}</p>".encode())

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    return result, server.server_address[1], server


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("credentials", nargs="?", help="client_secret.json scaricato da Google")
    ap.add_argument("--client-id")
    args = ap.parse_args()
    if args.credentials:
        with open(args.credentials) as f:
            data = json.load(f)
        client = data.get("installed") or data.get("web") or {}
        args.client_id = client.get("client_id", "")
        client_secret = client.get("client_secret", "")
        if not (args.client_id and client_secret):
            print("Il file non contiene client_id e client_secret.")
            return 1
    elif args.client_id:
        client_secret = getpass.getpass("Client secret (non viene mostrato): ").strip()
    else:
        ap.error("passa il file client_secret.json oppure --client-id")

    state = secrets.token_urlsafe(16)
    result, port, server = _wait_for_code(state)
    redirect_uri = f"http://127.0.0.1:{port}"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": args.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        # offline + consent: senza questi Google non rilascia il refresh token.
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })
    print("\nSi apre il browser. Accedi con l'account Gmail che riceve la posta di info@.")
    print("Se non si apre, copia questo indirizzo nel browser:\n\n" + url + "\n")
    webbrowser.open(url)
    while not result:
        server.handle_request()
    server.server_close()
    if "error" in result:
        print("Autorizzazione non riuscita:", result["error"])
        return 1

    body = urllib.parse.urlencode({
        "code": result["code"],
        "client_id": args.client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body)) as r:
        tokens = json.load(r)
    refresh = tokens.get("refresh_token")
    if not refresh:
        print("Google non ha restituito un refresh token. Revoca l'accesso all'app su "
              "https://myaccount.google.com/permissions e riprova.")
        return 1
    print("\nFatto. Su Railway: servizio del bot → Variables → Raw Editor, incolla queste")
    print("righe in fondo e salva. Non condividerle in chat.\n")
    print(f"GMAIL_CLIENT_ID={args.client_id}")
    print(f"GMAIL_CLIENT_SECRET={client_secret}")
    print(f"GMAIL_REFRESH_TOKEN={refresh}")
    print("MAIL_AUTORESPONDER_ENABLED=true")
    print("MAIL_DRAFT_ONLY=true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
