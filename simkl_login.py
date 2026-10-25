#!/usr/bin/env python3
"""
One-time Simkl sign-in. Prints the refresh token to save as the SIMKL_REFRESH_TOKEN secret.

    SIMKL_CLIENT_ID=xxxx python simkl_login.py

1. Register an app at https://simkl.com/settings/developer/ as type
   "TV, devices & command line" and copy its Client ID.
2. Run this script, open the link it prints, approve.
3. Paste the printed refresh token into GitHub as the SIMKL_REFRESH_TOKEN secret.

The token is read-only (scope media:read). It stays valid as long as the nightly job keeps
using it (180 days, reset on every use). Never commit it to the repo: Simkl automatically
revokes tokens that show up on GitHub.
"""
import base64
import hashlib
import os
import secrets
import sys
import time

import requests

API = "https://api.simkl.com"
APP = {"app-name": "my-nuvio-art", "app-version": "1.0"}


def main() -> int:
    cid = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SIMKL_CLIENT_ID", "")).strip()
    if not cid:
        print("Set SIMKL_CLIENT_ID (or pass it as the first argument).", file=sys.stderr)
        return 2

    s = requests.Session()
    s.headers["User-Agent"] = f"{APP['app-name']}/{APP['app-version']}"
    q = {"client_id": cid, **APP}

    # Simkl's docs say the V2 device flow uses PKCE; send it, and retry without if it's refused.
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    body = {"client_id": cid, "scope": "media:read",
            "code_challenge": challenge, "code_challenge_method": "S256"}
    r = s.post(f"{API}/oauth2/device", params=q, data=body, timeout=20)
    if r.status_code == 400:
        body = {"client_id": cid, "scope": "media:read"}
        verifier = None
        r = s.post(f"{API}/oauth2/device", params=q, data=body, timeout=20)
    if r.status_code != 200:
        print(f"Couldn't start sign-in ({r.status_code}): {r.text[:300]}", file=sys.stderr)
        if r.status_code == 401:
            print("Check the Client ID, and that the app is registered as "
                  "'TV, devices & command line'.", file=sys.stderr)
        return 1

    d = r.json()
    print("\nOpen this link and approve the request:\n")
    print(f"    {d.get('verification_uri_complete') or d['verification_uri']}")
    print(f"\nor go to {d['verification_uri']} and enter the code  {d['user_code']}\n")
    print("Waiting for approval...", flush=True)

    interval = int(d.get("interval", 5))
    deadline = time.time() + int(d.get("expires_in", 900))
    poll = {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": d["device_code"], "client_id": cid}
    if verifier:
        poll["code_verifier"] = verifier

    while time.time() < deadline:
        time.sleep(interval)
        t = s.post(f"{API}/oauth2/token", params=q, data=poll, timeout=20)
        if t.status_code == 200:
            tok = t.json()
            print("\nSigned in. Save this as the SIMKL_REFRESH_TOKEN repository secret:\n")
            print(f"    {tok['refresh_token']}\n")
            print("(Don't commit it to the repo; Simkl revokes tokens that appear on GitHub.)")
            return 0
        err = (t.json() if t.content else {}).get("error", "")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        print(f"\nSign-in failed ({t.status_code}): {t.text[:300]}", file=sys.stderr)
        return 1

    print("\nThe code expired before it was approved. Run the script again.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
