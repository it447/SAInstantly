import base64
import os
import time
from email.mime.text import MIMEText
from urllib.parse import urlencode

import requests

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


def build_auth_url(state):
    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": os.environ["GOOGLE_REDIRECT_URI"],
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code):
    resp = requests.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "redirect_uri": os.environ["GOOGLE_REDIRECT_URI"],
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(refresh_token):
    resp = requests.post(
        TOKEN_URL,
        data={
            "refresh_token": refresh_token,
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_user_email(access_token):
    resp = requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("email")


def get_valid_access_token(account):
    """account: dict with access_token, refresh_token, token_expires_at.
    Returns (access_token, refreshed_fields_or_None).
    """
    if account.get("token_expires_at", 0) > time.time() + 60:
        return account["access_token"], None
    tokens = refresh_access_token(account["refresh_token"])
    updated = {
        "access_token": tokens["access_token"],
        "token_expires_at": time.time() + tokens.get("expires_in", 3600),
    }
    return updated["access_token"], updated


def _build_raw_message(from_email, to_email, subject, body_text, thread_headers=None):
    msg = MIMEText(body_text, "plain")
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    if thread_headers:
        if thread_headers.get("message_id"):
            msg["In-Reply-To"] = thread_headers["message_id"]
            msg["References"] = thread_headers["message_id"]
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return raw


def send_message(access_token, from_email, to_email, subject, body_text, thread_id=None, in_reply_to_message_id=None):
    raw = _build_raw_message(
        from_email,
        to_email,
        subject,
        body_text,
        thread_headers={"message_id": in_reply_to_message_id} if in_reply_to_message_id else None,
    )
    payload = {"raw": raw}
    if thread_id:
        payload["threadId"] = thread_id
    resp = requests.post(
        f"{GMAIL_API}/messages/send",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json=payload,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def get_message(access_token, message_id):
    resp = requests.get(
        f"{GMAIL_API}/messages/{message_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"format": "metadata", "metadataHeaders": ["Message-ID"]},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_thread(access_token, thread_id):
    resp = requests.get(
        f"{GMAIL_API}/threads/{thread_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"format": "metadata", "metadataHeaders": ["From", "Message-ID"]},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()
