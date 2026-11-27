"""
Unified CLI Login Utility for YouTube, Canva, and Instagram (GTA6 Viral Clipper Pro).
Handles interactive and command-line OAuth 2.0 authentication for both services,
saving tokens and updating .env and config.json.
"""

import os
import sys
import json
import base64
import hashlib
import secrets
import argparse
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import requests

from config import update_env_file
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, ".env")
CLIENT_SECRET_FILE = os.path.join(BASE_DIR, "client_secret.json")
YOUTUBE_TOKEN_FILE = os.path.join(BASE_DIR, "token.json")
CANVA_TOKEN_FILE = os.path.join(BASE_DIR, "canva_token.json")

load_dotenv(ENV_FILE)

def update_env_variable(key: str, value: str):
    """Updates or appends a key-value pair in the .env file."""
    update_env_file(ENV_FILE, {key: value})

def find_client_secret_file() -> str:
    """Delegate client secret resolution (glob or .env fallback) to youtube_uploader."""
    from youtube_uploader import find_client_secret_file as yt_find_secret
    return yt_find_secret() or ""

# =====================================================================
# YOUTUBE OAUTH 2.0 CLI LOGIN
# =====================================================================

def login_youtube():
    print("\n=======================================================")
    print("        🔴 YOUTUBE DATA API v3 - CLI LOGIN")
    print("=======================================================")

    from google_auth_oauthlib.flow import InstalledAppFlow

    SCOPES = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly"
    ]

    detected_secret = find_client_secret_file()

    if not detected_secret:
        c_id = os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
        c_sec = os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()

        if not c_id or c_id == "your_youtube_client_id_here" or not c_sec or c_sec == "your_youtube_client_secret_here":
            print("\n[!] No client secret JSON file found in project folder.")
            print("You can upload/drop your 'client_secret.json' file into:")
            print(f"    {BASE_DIR}")
            print("OR enter your Google Cloud OAuth Client ID & Secret below:")
            c_id = input("\nEnter YouTube Client ID (or press Enter if uploading file): ").strip()
            
            if not c_id:
                # Check again if user placed file while prompt was waiting
                detected_secret = find_client_secret_file()
                if not detected_secret:
                    print("\n[INFO] Please drop your client_secret.json into this folder and re-run.")
                    return False
            else:
                c_sec = input("Enter YouTube Client Secret: ").strip()
                if not c_sec:
                    print("\n[ERROR] Client Secret is required. Aborting.")
                    return False

                update_env_variable("YOUTUBE_CLIENT_ID", c_id)
                update_env_variable("YOUTUBE_CLIENT_SECRET", c_sec)

                client_config = {
                    "installed": {
                        "client_id": c_id,
                        "client_secret": c_sec,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "redirect_uris": ["http://localhost"]
                    }
                }
                with open(CLIENT_SECRET_FILE, "w", encoding="utf-8") as f:
                    json.dump(client_config, f, indent=4)
                detected_secret = CLIENT_SECRET_FILE
                print(f"[OK] Generated {CLIENT_SECRET_FILE} from provided credentials.")
        else:
            client_config = {
                "installed": {
                    "client_id": c_id,
                    "client_secret": c_sec,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"]
                }
            }
            with open(CLIENT_SECRET_FILE, "w", encoding="utf-8") as f:
                json.dump(client_config, f, indent=4)
            detected_secret = CLIENT_SECRET_FILE

    print(f"\n[OK] Using client secret: {os.path.basename(detected_secret)}")

    try:
        print("\n[>>] Initializing Google OAuth 2.0 authorization server...")
        flow = InstalledAppFlow.from_client_secrets_file(detected_secret, SCOPES)
        
        print("[>>] Opening your default browser for Google sign-in...")
        print("     Log in and click 'Allow' to grant YouTube upload permissions.")
        creds = flow.run_local_server(port=0, open_browser=True)

        with open(YOUTUBE_TOKEN_FILE, "w", encoding="utf-8") as tf:
            tf.write(creds.to_json())

        update_env_variable("YOUTUBE_OAUTH_CONNECTED", "true")

        print("\n=======================================================")
        print("  ✅ YOUTUBE LOGIN SUCCESSFUL!")
        print(f"  Token saved to: {YOUTUBE_TOKEN_FILE}")
        print("  1-Click YouTube Shorts Upload is now fully active!")
        print("=======================================================\n")
        return True

    except Exception as e:
        print(f"\n[ERROR] YouTube login failed: {e}")
        return False


# =====================================================================
# CANVA CONNECT API OAUTH 2.0 PKCE CLI LOGIN
# =====================================================================

class CanvaOAuthHandler(BaseHTTPRequestHandler):
    auth_code = None
    error = None

    def do_GET(self):
        query = urlparse(self.path).query
        params = parse_qs(query)

        if "code" in params:
            CanvaOAuthHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"""
            <html>
            <body style="font-family: sans-serif; text-align: center; padding-top: 50px; background: #0e1318; color: #fff;">
                <h1 style="color: #00c4cc;">Canva Authorization Successful! &#10004;</h1>
                <p>You can close this tab and return to the GTA6 Viral Clipper Pro terminal.</p>
            </body>
            </html>
            """)
        else:
            CanvaOAuthHandler.error = params.get("error_description", ["Unknown error"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<h1>Authorization Failed: {CanvaOAuthHandler.error}</h1>".encode("utf-8"))

    def log_message(self, format, *args):
        return

def generate_pkce_pair():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("utf-8").replace("=", "")
    return verifier, challenge

def login_canva():
    print("\n=======================================================")
    print("        🎨 CANVA CONNECT API & MCP - CLI LOGIN")
    print("=======================================================")

    c_id = os.environ.get("CANVA_CLIENT_ID", "").strip()
    c_sec = os.environ.get("CANVA_CLIENT_SECRET", "").strip()

    if not c_id or c_id == "your_canva_client_id_here" or not c_sec or c_sec == "your_canva_client_secret_here":
        print("\n[!] No Canva Connect API credentials found in .env.")
        print("Obtain your Client ID and Client Secret from: https://www.canva.com/developers/")
        c_id = input("Enter Canva Client ID: ").strip()
        c_sec = input("Enter Canva Client Secret: ").strip()

        if not c_id or not c_sec:
            print("\n[ERROR] Both Canva Client ID and Client Secret are required. Aborting.")
            return False

        update_env_variable("CANVA_CLIENT_ID", c_id)
        update_env_variable("CANVA_CLIENT_SECRET", c_sec)

    code_verifier, code_challenge = generate_pkce_pair()
    redirect_uri = "http://127.0.0.1:8088/oauth/callback"
    scopes = "design:content:read design:content:write folder:read folder:write asset:read asset:write brandtemplate:content:read"

    server_address = ("127.0.0.1", 8088)
    try:
        httpd = HTTPServer(server_address, CanvaOAuthHandler)
    except Exception as e:
        print(f"[ERROR] Could not start local redirect server on port 8088: {e}")
        return False

    auth_url = (
        f"https://www.canva.com/api/oauth/authorize?"
        f"code_challenge={code_challenge}&"
        f"code_challenge_method=s256&"
        f"scope={scopes.replace(' ', '%20')}&"
        f"response_type=code&"
        f"client_id={c_id}&"
        f"redirect_uri={redirect_uri}"
    )

    print("\n[>>] Starting local callback listener at http://127.0.0.1:8088/oauth/callback ...")
    print("[>>] Opening Canva authorization page in your browser...")
    print(f"\nIf your browser does not open automatically, visit this URL:\n{auth_url}\n")
    webbrowser.open(auth_url)

    print("[>>] Waiting for Canva authorization callback in your browser...")
    httpd.handle_request()

    auth_code = CanvaOAuthHandler.auth_code
    if not auth_code:
        print(f"\n[ERROR] Did not receive authorization code. Error: {CanvaOAuthHandler.error}")
        return False

    print("\n[>>] Exchanging authorization code for Canva Access & Refresh tokens...")
    token_url = "https://api.canva.com/rest/v1/oauth/token"
    
    auth_header = base64.b64encode(f"{c_id}:{c_sec}".encode("utf-8")).decode("utf-8")
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
        "code": auth_code,
        "redirect_uri": redirect_uri
    }

    try:
        httpd.server_close()
    except Exception:
        pass

    try:
        resp = requests.post(token_url, headers=headers, data=data, timeout=30)
        token_data = resp.json()

        if resp.status_code == 200 and "access_token" in token_data:
            with open(CANVA_TOKEN_FILE, "w", encoding="utf-8") as f:
                json.dump(token_data, f, indent=4)

            update_env_variable("CANVA_ACCESS_TOKEN", token_data["access_token"])
            if "refresh_token" in token_data:
                update_env_variable("CANVA_REFRESH_TOKEN", token_data["refresh_token"])
            update_env_variable("CANVA_OAUTH_CONNECTED", "true")

            print("\n=======================================================")
            print("  ✅ CANVA LOGIN SUCCESSFUL!")
            print(f"  Token saved to: {CANVA_TOKEN_FILE}")
            print("  Canva Pro MCP integration is now authorized!")
            print("=======================================================\n")
            return True
        else:
            print(f"\n[ERROR] Failed to exchange token: {token_data}")
            return False
    except Exception as e:
        print(f"\n[ERROR] Request to Canva token endpoint failed: {e}")
        return False

# =====================================================================
# INSTAGRAM CLI LOGIN
# =====================================================================

def login_instagram():
    print("\n=======================================================")
    print("        📸 INSTAGRAM REELS - CLI LOGIN")
    print("=======================================================")
    print("Choose login method:")
    print("1. Direct Account Login (Username & Password)")
    print("2. Meta Graph API (Access Token & Account ID)")
    method = input("Select [1-2] (default: 1): ").strip() or "1"

    if method == "1":
        u = input("Enter Instagram Username: ").strip()
        import getpass
        try:
            p = getpass.getpass("Enter Instagram Password: ").strip()
        except Exception:
            p = input("Enter Instagram Password: ").strip()

        if not u or not p:
            print("[ERROR] Username and password required.")
            return False

        from instagram_uploader import login_instagram_direct
        res = login_instagram_direct(u, p)
        if res.get("status") == "two_factor_required":
            code = input("Enter 2FA Verification Code: ").strip()
            res = login_instagram_direct(u, p, verification_code=code)

        if res.get("status") == "success":
            print(f"\n✅ {res.get('message')}")
            return True
        else:
            print(f"\n❌ Login failed: {res.get('message')}")
            return False
    else:
        token = input("Enter Meta Access Token: ").strip()
        act_id = input("Enter Instagram Account ID: ").strip()
        if not token or not act_id:
            print("[ERROR] Access Token and Account ID required.")
            return False
        from instagram_uploader import save_instagram_credentials
        save_instagram_credentials(access_token=token, account_id=act_id)
        print("\n✅ Meta Graph API credentials saved to .env!")
        return True

def main():
    parser = argparse.ArgumentParser(description="GTA6 Viral Clipper Pro - CLI Login Utility")
    parser.add_argument(
        "--service",
        choices=["youtube", "canva", "instagram", "all"],
        help="Service to authenticate (youtube, canva, instagram, or all)"
    )
    args = parser.parse_args()

    service = args.service

    if not service:
        print("\n=======================================================")
        print("        🔑 GTA6 VIRAL CLIPPER PRO - AUTHENTICATION")
        print("=======================================================")
        print("1. Log in to YouTube (YouTube Shorts Data API v3)")
        print("2. Log in to Canva (Canva Pro Connect API & MCP)")
        print("3. Log in to Instagram (Instagram Reels Direct / Graph API)")
        print("4. Log in to All Services")
        print("5. Exit")
        choice = input("\nSelect an option [1-5]: ").strip()

        if choice == "1":
            service = "youtube"
        elif choice == "2":
            service = "canva"
        elif choice == "3":
            service = "instagram"
        elif choice == "4":
            service = "all"
        else:
            print("Exiting.")
            sys.exit(0)

    if service in ["youtube", "all"]:
        login_youtube()

    if service in ["canva", "all"]:
        login_canva()

    if service in ["instagram", "all"]:
        login_instagram()


if __name__ == "__main__":
    main()
