"""
Plaid Account Linker

Run this once per bank/card you want to connect.
It opens a browser to Plaid Link, then saves the access token locally
in tokens.json (which is git-ignored).
"""

import json
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import plaid
from dotenv import load_dotenv
from plaid.api import plaid_api
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products

BASE_DIR = Path(__file__).parent
TOKENS_FILE = BASE_DIR / "tokens.json"
PORT = 8765

ENV_MAP = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}

# Plaid Link page. It reads the link token from this page's own URL
# (?link_token=...), so nothing needs to be injected into the HTML.
LINK_HTML = """<!DOCTYPE html>
<html>
<head><title>Plaid Link</title></head>
<body>
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
<script>
  const params = new URLSearchParams(window.location.search);
  const handler = Plaid.create({
    token: params.get('link_token'),
    onSuccess: (public_token) => {
      fetch('/exchange?public_token=' + encodeURIComponent(public_token))
        .then(r => r.text())
        .then(msg => { document.body.textContent = msg; });
    },
    onExit: () => {
      document.body.textContent = 'Cancelled or error. You can close this window.';
    },
  });
  handler.open();
</script>
</body>
</html>
"""


# ── Setup ─────────────────────────────────────────────────────────────────────

def build_client() -> plaid_api.PlaidApi:
    """Create a Plaid client from environment variables, or exit with a clear message."""
    load_dotenv(dotenv_path=BASE_DIR / ".env")

    client_id = os.getenv("PLAID_CLIENT_ID")
    secret = os.getenv("PLAID_SECRET")
    env_name = os.getenv("PLAID_ENV", "sandbox").lower()

    missing = [
        name
        for name, value in (("PLAID_CLIENT_ID", client_id), ("PLAID_SECRET", secret))
        if not value
    ]
    if missing:
        sys.exit(f"Missing {', '.join(missing)}. Copy .env.example to .env and fill it in.")

    if env_name not in ENV_MAP:
        sys.exit(f"Unsupported PLAID_ENV '{env_name}'. Use one of: {', '.join(ENV_MAP)}.")

    configuration = plaid.Configuration(
        host=ENV_MAP[env_name],
        api_key={"clientId": client_id, "secret": secret},
    )
    return plaid_api.PlaidApi(plaid.ApiClient(configuration))


# ── Token storage ─────────────────────────────────────────────────────────────

def load_tokens() -> dict:
    try:
        with open(TOKENS_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"access_tokens": []}


def save_token(access_token: str) -> None:
    data = load_tokens()
    if access_token not in data["access_tokens"]:
        data["access_tokens"].append(access_token)
    with open(TOKENS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    TOKENS_FILE.chmod(0o600)  # readable by you only (no-op on Windows)
    print(f"Saved access token to {TOKENS_FILE.name}")


# ── Plaid calls ───────────────────────────────────────────────────────────────

def create_link_token(client: plaid_api.PlaidApi) -> str:
    request = LinkTokenCreateRequest(
        products=[Products("transactions")],
        client_name="My Budget App",
        country_codes=[CountryCode("US")],
        language="en",
        user=LinkTokenCreateRequestUser(client_user_id="local-user")
    )
    return client.link_token_create(request)["link_token"]


# ── Local server that receives the public token from Plaid Link ───────────────

def make_handler(client: plaid_api.PlaidApi, state: dict):
    """Build a request handler class bound to this client and shared state."""

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, content_type: str, body: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self):
            parsed = urlparse(self.path)

            if parsed.path == "/link":
                self._send(200, "text/html", LINK_HTML)

            elif parsed.path == "/exchange":
                public_token = parse_qs(parsed.query).get("public_token", [""])[0]
                try:
                    response = client.item_public_token_exchange(
                        ItemPublicTokenExchangeRequest(public_token=public_token)
                    )
                    save_token(response["access_token"])
                    state["done"] = True
                    message = "Account linked successfully! You can close this window."
                except Exception as e:
                    message = f"Error linking account: {e}"
                self._send(200, "text/plain", message)

            else:
                self._send(404, "text/plain", "Not found")

        def log_message(self, *args):
            pass  # suppress per-request server logs

    return Handler


def main() -> None:
    client = build_client()

    print("Creating Plaid Link token…")
    link_token = create_link_token(client)
    url = f"http://localhost:{PORT}/link?link_token={link_token}"

    print("Opening browser to connect your account…")
    print(f"(If it doesn't open automatically, visit: http://localhost:{PORT}/link?link_token=…)")
    print("(Copy the full link from your browser if you need to reopen it.)\n")

    state = {"done": False}
    server = HTTPServer(("127.0.0.1", PORT), make_handler(client, state))
    webbrowser.open(url)

    try:
        while not state["done"]:
            server.handle_request()
    except KeyboardInterrupt:
        print("\nCancelled.")
        return
    finally:
        server.server_close()

    print("\nAll done! Run sync.py to fetch transactions.")


if __name__ == "__main__":
    main()