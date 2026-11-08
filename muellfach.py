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
    """Removes script, style and HTML tags, preserves links, and unescapes entities"""
    if not html_content:
        return ""
    # remove scripts and styles
    clean = re.sub(r'<(script|style)[^>]*>[\s\S]*?</\1>', '', html_content, flags=re.IGNORECASE)
    # preserve links
    def _link_sub(m):
        href = m.group(1).strip()
        label = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        if not label or label.lower() in ('click here', 'link', 'here', 'verify', 'confirm'):
            return f" {href} "
        if href in label:
            return f" {label} "
        return f" {label} ({href}) "
    clean = re.sub(r'<a\s+[^>]*href=["\'](https?://[^"\']+)["\'][^>]*>([\s\S]*?)</a>', _link_sub, clean, flags=re.IGNORECASE)
    # replace block elements with newlines
    clean = re.sub(r'<(?:br|p|div|tr|li|h[1-6])[\s/]*>', '\n', clean, flags=re.IGNORECASE)
    # strip remaining tags
    clean = re.sub(r'<[^>]+>', ' ', clean)
    # decode HTML entities
    text = html.unescape(clean)
    # normalize whitespace
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return '\n'.join(lines)

STOPWORDS = {
    'lautet', 'please', 'thanks', 'verify', 'verifying', 'action', 'support',
    'security', 'account', 'expire', 'expires', 'minutes', 'seconds', 'button',
    'client', 'device', 'request', 'browser', 'system', 'service', 'online',
    'portal', 'update', 'status', 'sender', 'domain', 'mailer', 'notice'
}

def extract_otp_code(text: str) -> str | None:
    """Extracts genuine OTP / verification codes from email text or subject with 0 fake codes"""
    if not text:
        return None

    # Remove URLs so query params / tracking IDs are not mistaken for OTPs
    clean = re.sub(r'https?://\S+', ' ', text)
    clean = re.sub(r'<[^>]+>', ' ', clean)
    clean = re.sub(r'[\r\n\t]+', ' ', clean)

    # 1. Instagram specific pattern: '123 456 is your Instagram code' or '123456 is your Instagram code'
    ig_m = re.search(r'\b([0-9]{3}\s?[0-9]{3})\b\s+(?:is\s+your\s+instagram\s+code|to\s+verify\s+your\s+instagram)', clean, re.IGNORECASE)
    if ig_m:
        return ig_m.group(1).replace(' ', '')

    # 2. Provider prefix patterns: 'G-123456', 'FB-12345', 'V-123456'
    m = re.search(r'\b([A-Z]{1,3}-[0-9]{5,8})\b', clean)
    if m:
        return m.group(1)

    # 3. Number before keyword: '123456 is your (verification|security|login|...) code/otp'
    m = re.search(
        r'\b([0-9]{4,8}|[0-9]{3}[-\s][0-9]{3})\b(?:\s+(?:is|ist)\s+(?:your|dein|ihr|the)\s+[\w\s]{0,25}?(?:code|otp|pin|passcode|bestätigungscode))',
        clean,
        re.IGNORECASE
    )
    if m:
        val = m.group(1).replace(' ', '').replace('-', '')
        if not (val.isdigit() and int(val) in range(1950, 2035)):
            return val

    # 4. Keyword followed by code:
    kw = (
        r'(?:verification\s*code|security\s*code|confirm(?:ation)?\s*code|one[- ]time\s*(?:pass(?:word|code)|code)|'
        r'bestätigungscode|sicherheitscode|prüfcode|verifizierungscode|'
        r'dein\s*code|ihr\s*code|instagram\s*code|'
        r'otp|passcode|auth(?:entication)?\s*code|login\s*code|access\s*code|pin\s*code|'
        r'\bcode\b|\bpin\b|\botp\b)'
    )
    pattern = (
        kw
        + r'(?:[^\w\n\r]{0,5}|(?:\s+(?:is|ist|lautet|to\s*verify|for|your|dein|ihr|number|is\s*your|:)[\s:=—–#-]*)+)'
        + r'[:\s=—–#-]*\s*([0-9]{4,8}|[0-9]{3}[-\s][0-9]{3}|(?=[A-Z0-9]*[0-9])(?=[A-Z0-9]*[A-Z])[A-Z0-9]{5,8})\b'
    )
    m = re.search(pattern, clean, re.IGNORECASE)
    if m:
        val = m.group(1).replace(' ', '').replace('-', '')
        if val.lower() not in STOPWORDS:
            if not (val.isdigit() and int(val) in range(1950, 2035)):
                return val

    # 4. Action phrase: 'Use 123456 to verify/sign in' or 'Enter 123456 to continue'
    m = re.search(
        r'\b(?:use|enter|input|geben\s+sie)\s+([0-9]{4,8}|[0-9]{3}[-\s][0-9]{3})\b(?:\s+to\s+(?:verify|sign|log|confirm|continue))',
        clean,
        re.IGNORECASE
    )
    if m:
        val = m.group(1).replace(' ', '').replace('-', '')
        if not (val.isdigit() and int(val) in range(1950, 2035)):
            return val

    return None
