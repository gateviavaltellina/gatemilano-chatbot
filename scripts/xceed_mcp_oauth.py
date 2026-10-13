"""Collega il bot all'MCP di Xceed. Da lanciare UNA volta, in locale, con un browser.

Cosa fa:
1. registra il bot come applicazione sul server MCP di Xceed (registrazione dinamica);
2. apre il browser per il login Xceed e riceve il consenso su 127.0.0.1;
3. elenca gli strumenti disponibili (servono per capire come si fa un rimborso) e li
   salva in xceed_mcp_tools.json — quel file NON contiene segreti, si può condividere;
4. stampa le righe da incollare su Railway (quelle sì sono segrete).

Solo libreria standard:   python3 xceed_mcp_oauth.py
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

BASE = "https://mcp.xceed-services.me"
RESOURCE = f"{BASE}/mcp"


def _post(url: str, data: dict | None = None, *, form: bool = False, headers=None):
    body = (urllib.parse.urlencode(data) if form else json.dumps(data)).encode()
    h = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json",
         "Accept": "application/json, text/event-stream"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    with urllib.request.urlopen(req) as r:
        raw = r.read().decode("utf-8", "replace")
        return r.headers, raw


def _json_rpc_result(raw: str) -> dict:
    """Le risposte MCP possono arrivare come JSON o come stream SSE ("data: {...}")."""
    raw = raw.strip()
    if raw.startswith("{"):
        return json.loads(raw)
    for line in raw.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload.startswith("{"):
                return json.loads(payload)
    raise ValueError("Risposta MCP non riconosciuta: " + raw[:200])


def _wait_for_code(state: str):
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
            ok = "code" in result
            self.wfile.write(("<p>Fatto, puoi chiudere questa scheda.</p>" if ok
                              else "<p>Errore: torna al terminale.</p>").encode())

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    return result, server


def main() -> int:
    state = secrets.token_urlsafe(16)
    result, server = _wait_for_code(state)
    redirect_uri = f"http://127.0.0.1:{server.server_address[1]}/callback"

    # 1. Registrazione dinamica del client (pubblico, con PKCE).
    _, raw = _post(f"{BASE}/register", {
        "client_name": "Gate Milano chatbot",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    })
    client_id = json.loads(raw)["client_id"]

    # 2. Consenso nel browser.
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    url = f"{BASE}/authorize?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        "resource": RESOURCE,
    })
    print("\nSi apre il browser: accedi con l'account Xceed di Gate Milano e autorizza.")
    print("Se non si apre, copia questo indirizzo nel browser:\n\n" + url + "\n")
    webbrowser.open(url)
    while not result:
        server.handle_request()
    server.server_close()
    if "error" in result:
        print("Autorizzazione non riuscita:", result["error"])
        return 1

    _, raw = _post(f"{BASE}/token", {
        "grant_type": "authorization_code", "code": result["code"],
        "redirect_uri": redirect_uri, "client_id": client_id,
        "code_verifier": verifier, "resource": RESOURCE,
    }, form=True)
    tokens = json.loads(raw)
    access, refresh = tokens["access_token"], tokens.get("refresh_token", "")

    # 3. Elenco strumenti.
    auth = {"Authorization": f"Bearer {access}"}
    headers, raw = _post(RESOURCE, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "gate-milano-chatbot", "version": "1"}},
    }, headers=auth)
    session = headers.get("Mcp-Session-Id")
    if session:
        auth["Mcp-Session-Id"] = session
    try:
        _post(RESOURCE, {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=auth)
    except urllib.error.HTTPError:
        pass
    _, raw = _post(RESOURCE, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=auth)
    tools = _json_rpc_result(raw).get("result", {}).get("tools", [])
    with open("xceed_mcp_tools.json", "w", encoding="utf-8") as f:
        json.dump(tools, f, ensure_ascii=False, indent=2)
    print(f"\n{len(tools)} strumenti disponibili (salvati in xceed_mcp_tools.json):")
    for t in tools:
        print(f"  - {t.get('name')}: {(t.get('description') or '').splitlines()[0][:100] if t.get('description') else ''}")

    print("\nOra su Railway (servizio del bot → Variables → Raw Editor) incolla queste righe.")
    print("Non condividerle in chat. Il file xceed_mcp_tools.json invece mandalo pure.\n")
    print(f"XCEED_MCP_CLIENT_ID={client_id}")
    print(f"XCEED_MCP_REFRESH_TOKEN={refresh}")
    if not refresh:
        print("\n⚠️ Xceed non ha restituito un refresh token: dimmelo, va gestito diversamente.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
