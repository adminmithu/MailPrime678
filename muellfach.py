import re
import html
import httpx
from config import MUELLFACH_SYNC_URL, MUELLFACH_API_URL

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "https://www.muellfach.de/",
}

async def sync_mailbox(client: httpx.AsyncClient):
    """Triggers IMAP fetch on muellfach.de server"""
    try:
        resp = await client.get(MUELLFACH_SYNC_URL, headers=HEADERS, timeout=8.0)
        return resp.status_code == 200
    except Exception:
        return False

async def get_mailbox_messages(client: httpx.AsyncClient, email: str):
    """Fetches list of received emails for a given address"""
    try:
        resp = await client.get(
            MUELLFACH_API_URL,
            params={"action": "mails", "address": email},
            headers=HEADERS,
            timeout=8.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "ok":
                return data.get("mails", [])
    except Exception:
        pass
    return []

async def get_single_mail(client: httpx.AsyncClient, email: str, mail_id: int):
    """Fetches full content of an email by id"""
    try:
        resp = await client.get(
            MUELLFACH_API_URL,
            params={"action": "mail", "id": mail_id, "address": email},
            headers=HEADERS,
            timeout=8.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "ok" and data.get("mail"):
                return data["mail"]
    except Exception:
        pass
    return None

def strip_html_tags(html_content: str) -> str:
    """Removes script, style and HTML tags and unescapes entities"""
    if not html_content:
        return ""
    # remove scripts and styles
    clean = re.sub(r'<(script|style)[^>]*>[\s\S]*?</\1>', '', html_content, flags=re.IGNORECASE)
    # replace br and block elements with newlines
    clean = re.sub(r'<(?:br|p|div|tr|li)[\s/]*>', '\n', clean, flags=re.IGNORECASE)
    # strip remaining tags
    clean = re.sub(r'<[^>]+>', ' ', clean)
    # decode HTML entities
    text = html.unescape(clean)
    # normalize whitespace
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return '\n'.join(lines)

def extract_otp_code(text: str) -> str | None:
    """Extracts OTP / verification codes from email text or subject"""
    if not text:
        return None
        
    # High-confidence pattern: preceded by code/otp keywords
    pattern_keyword = re.search(
        r'(?:code|otp|verification|passcode|pin|token|bestätigungscode|code\s*is|dein\s*code)[\s:=#\-]+([0-9]{4,8}|[A-Z0-9]{5,8})\b',
        text,
        re.IGNORECASE
    )
    if pattern_keyword:
        return pattern_keyword.group(1).strip()
        
    # Fallback pattern: 4 to 8 digit standalone numbers with spaces/hyphens
    digits = re.findall(r'\b(?:\d{3}[-\s]\d{3}|\d{4,8})\b', text)
    if digits:
        for d in digits:
            code = d.replace(' ', '').replace('-', '')
            # Skip common years
            if code in ("2023", "2024", "2025", "2026", "2027", "2028", "2029", "2030"):
                continue
            return code
        
    return None
