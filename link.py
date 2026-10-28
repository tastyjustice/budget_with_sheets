"""
Plaid Account Linker
Run this once per bank/card you want to connect.
It opens a browser to Plaid Link, then saves the access token locally.
"""

import os
import json
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv


import plaid
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.products import Products
from plaid.model.country_code import CountryCode

from pathlib import Path
load_dotenv(dotenv_path=Path(__file__).parent / ".env")


PLAID_ENV = os.getenv("PLAID_ENV", "sandbox")
ENV_MAP = {
    "sandbox":     plaid.Environment.Sandbox,
    "production":  plaid.Environment.Production,
}

configuration = plaid.Configuration(
    host=ENV_MAP[PLAID_ENV],
    api_key={
        "clientId": os.getenv("PLAID_CLIENT_ID"),
        "secret":   os.getenv("PLAID_SECRET"),
    },
)

api_client = plaid.ApiClient(configuration)
client = plaid_api.PlaidApi(api_client)
print("ENV_MAP lookup:", ENV_MAP.get(os.getenv("PLAID_ENV")))
print("reached line 46")

TOKENS_FILE = Path(__file__).parent / "tokens.json"
public_token_holder = {}


def load_tokens() -> dict:
    try:
        with open(TOKENS_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"access_tokens": []}


def save_token(access_token: str):
    data = load_tokens()
    if access_token not in data["access_tokens"]:
        data["access_tokens"].append(access_token)
    with open(TOKENS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved access token to {TOKENS_FILE}")


def create_link_token() -> str:
    request = LinkTokenCreateRequest(
        products=[Products("transactions")],
        client_name="My Budget App",
        country_codes=[CountryCode("US")],
        language="en",
        user=LinkTokenCreateRequestUser(client_user_id="local-user")
    )
    response = client.link_token_create(request)
    return response["link_token"]


# Minimal local server to capture the public token from Plaid Link
CALLBACK_HTML = """
<!DOCTYPE html>
<html>
<head><title>Plaid Link</title></head>
<body>
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
<script>
  const params = new URLSearchParams(window.location.search);
  const linkToken = params.get('link_token');
  const handler = Plaid.create({
    token: linkToken,
    onSuccess: (public_token, metadata) => {
      fetch('/exchange?public_token=' + public_token)
        .then(r => r.text())
        .then(msg => { document.body.innerHTML = '<h2>' + msg + '</h2>'; });
    },
    onExit: (err) => {
      document.body.innerHTML = '<h2>Cancelled or error. You can close this window.</h2>';
    },
  });
  handler.open();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/link":
            qs = parse_qs(parsed.query)
            link_token = qs.get("link_token", [""])[0]
            body = CALLBACK_HTML.replace("params.get('link_token')", f"'{link_token}'")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        elif parsed.path == "/exchange":
            qs = parse_qs(parsed.query)
            public_token = qs.get("public_token", [""])[0]
            try:
                req = ItemPublicTokenExchangeRequest(public_token=public_token)
                resp = client.item_public_token_exchange(req)
                access_token = resp["access_token"]
                save_token(access_token)
                public_token_holder["done"] = True
                msg = "Account linked successfully! You can close this window."
            except Exception as e:
                msg = f"Error: {e}"

            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(msg.encode())

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass  # suppress server logs


def main():
    print("Creating Plaid Link token…")
    link_token = create_link_token()

    url = f"http://localhost:8765/link?link_token={link_token}"
    print(f"\nOpening browser to connect your account…")
    print(f"(If it doesn't open automatically, visit: {url})\n")

    server = HTTPServer(("localhost", 8765), Handler)
    webbrowser.open(url)

    while not public_token_holder.get("done"):
        server.handle_request()

    print("\nAll done! Run sync.py to fetch transactions.")


if __name__ == "__main__":
    main()
