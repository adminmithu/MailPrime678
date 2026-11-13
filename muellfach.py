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

def clean_val(val: str) -> str:
    """Removes whitespace and hyphens from code string"""
    if not val:
        return ""
    return re.sub(r'[\s\-]+', '', val).strip()

def is_valid_otp(val: str) -> bool:
    """Validates extracted code: filters out years, lengths, and non-code words"""
    if not val:
        return False
    if val.isdigit():
        if len(val) == 4 and int(val) in range(1950, 2035):
            return False
        if len(val) < 4 or len(val) > 8:
            return False
    elif len(val) < 4 or len(val) > 10:
        return False
    if val.lower() in STOPWORDS:
        return False
    return True

def extract_otp_code(text: str, html_content: str = "") -> str | None:
    """
    Universal OTP/Code extractor supporting all global services and languages
    (Instagram, WhatsApp, Google, Facebook, Microsoft, Telegram, TikTok, Netflix,
     Binance, Steam, Discord, Apple, Amazon, OpenAI, Banks, etc.).
    """
    if not text and not html_content:
        return None

    combined_raw = f"{text or ''} {html_content or ''}"
    clean = re.sub(r'https?://\S+', ' ', combined_raw)
    clean = re.sub(r'<[^>]+>', ' ', clean)
    clean = re.sub(r'[\r\n\t]+', ' ', clean)

    # 1. Provider-prefixed codes: G-123456, FB-12345, V-123456, etc.
    m = re.search(r'\b([A-Z]{1,4}-[0-9]{4,8})\b', clean)
    if m:
        return m.group(1)

    # 2. Instagram: any 6-digit number in an Instagram email
    if 'instagram' in clean.lower():
        ig_any = re.search(r'\b([0-9]{3}\s?[0-9]{3})\b', clean)
        if ig_any and is_valid_otp(clean_val(ig_any.group(1))):
            return clean_val(ig_any.group(1))

    # 3. WhatsApp: 123-456 or 123 456
    if 'whatsapp' in clean.lower():
        wa = re.search(r'\b([0-9]{3}[-\s][0-9]{3})\b', clean)
        if wa and is_valid_otp(clean_val(wa.group(1))):
            return clean_val(wa.group(1))

    # 4. Steam Guard code: 5 uppercase letters/digits
    if 'steam' in clean.lower():
        stm = re.search(r'\b([A-Z0-9]{5})\b', clean)
        if stm and not stm.group(1).isdigit() and stm.group(1).lower() not in STOPWORDS:
            return stm.group(1)

    # 5. Telegram code: 5 digits
    if 'telegram' in clean.lower():
        tg = re.search(r'\b([0-9]{5})\b', clean)
        if tg and is_valid_otp(clean_val(tg.group(1))):
            return clean_val(tg.group(1))

    # 6. HTML visual highlights (Large fonts, header tags, code tags)
    if html_content:
        hl = re.search(
            r'(?:font-size:\s*(?:[2-9]\d|[1-9]\d{2})px|letter-spacing)[^>]*>[\s\n\r]*([0-9]{4,8}|[0-9]{3}[-\s][0-9]{3})[\s\n\r]*<',
            html_content,
            re.IGNORECASE,
        )
        if hl and is_valid_otp(clean_val(hl.group(1))):
            return clean_val(hl.group(1))
        hl_tag = re.search(
            r'<(?:h[1-3]|code|big|strong)\b[^>]*>[\s\n\r]*([0-9]{4,8}|[0-9]{3}[-\s][0-9]{3})[\s\n\r]*</(?:h[1-3]|code|big|strong)>',
            html_content,
            re.IGNORECASE,
        )
        if hl_tag and is_valid_otp(clean_val(hl_tag.group(1))):
            return clean_val(hl_tag.group(1))

    # 7. Multilingual Keyword Proximity Patterns
    # Covers: English, German, Bengali, Russian, Spanish, French, Italian, Turkish, Hindi, etc.
    kw_core = (
        r'(?:verification|security|confirm(?:ation)?|one[- ]time|bestätigung|sicherheit|'
        r'prüf|verif|código|clave|kod|auth(?:entication)?|login|sign[- ]in|aktiv|access|'
        r'validation|einmalkennwort|passcode|passwort|password|'
        r'код|подтвержд|проверочн|пароль|'
        r'কোড|ওটিপি|পিন|'
        r'\bcode\b|\botp\b|\bpin\b)'
    )

    # 7A. Number before keyword: '123456 is your verification code', '849201 ist Ihr Amazon Einmalkennwort'
    m_before = re.search(
        r'\b([0-9]{4,8}|[0-9]{3}[-\s][0-9]{3})\b[\s\w\W]{0,35}?'
        r'(?:is|ist|es|to|for|dein|ihr|your|le|la|ва|это|হলো|হল|হয়)?[\s:=-]*'
        + kw_core,
        clean,
        re.IGNORECASE,
    )
    if m_before and is_valid_otp(clean_val(m_before.group(1))):
        return clean_val(m_before.group(1))

    # 7B. Keyword before number: 'code: 123456', 'verification code 123456', 'код: 123456'
    m_after = re.search(
        kw_core
        + r'[\s\w\W]{0,35}?'
        r'[:\s=—–#-]+\s*'
        r'([0-9]{4,8}|[0-9]{3}[-\s][0-9]{3}|(?=[A-Z0-9]*[0-9])(?=[A-Z0-9]*[A-Z])[A-Z0-9]{5,8})\b',
        clean,
        re.IGNORECASE,
    )
    if m_after and is_valid_otp(clean_val(m_after.group(1))):
        return clean_val(m_after.group(1))

    # 8. Action phrases: 'enter 123456', 'use 123456', 'with 123456', 'geben sie 123456'
    m_action = re.search(
        r'\b(?:use|enter|input|geben\s+sie|utilisez|ingrese|with|mit|avec|c)\s+[:\s]*'
        r'([0-9]{4,8}|[0-9]{3}[-\s][0-9]{3})\b',
        clean,
        re.IGNORECASE,
    )
    if m_action and is_valid_otp(clean_val(m_action.group(1))):
        return clean_val(m_action.group(1))

    # 9. Short subject line checks (pure digits or code in subject)
    if len(clean) < 120:
        if re.match(r'^\s*([0-9]{4,8}|[0-9]{3}[-\s][0-9]{3})\s*$', clean):
            return clean_val(clean)
        has_subj_kw = bool(re.search(kw_core, clean, re.IGNORECASE))
        if has_subj_kw:
            subj_m = re.search(r'\b([0-9]{4,8}|[0-9]{3}[-\s][0-9]{3})\b', clean)
            if subj_m and is_valid_otp(clean_val(subj_m.group(1))):
                return clean_val(subj_m.group(1))

    # 10. Fallback: If email has ANY verification intent, pick the most prominent 4-8 digit number
    has_verify_intent = bool(re.search(
        r'(?:verify|verification|confirm|security|code|otp|pin|bestätig|sicherheit|einmalkennwort|код|কোড)',
        clean,
        re.IGNORECASE,
    ))
    if has_verify_intent:
        for p in [r'\b([0-9]{6}|[0-9]{3}[-\s][0-9]{3})\b', r'\b([0-9]{5})\b', r'\b([0-9]{4})\b', r'\b([0-9]{7,8})\b']:
            fb = re.search(p, clean)
            if fb and is_valid_otp(clean_val(fb.group(1))):
                return clean_val(fb.group(1))

    return None
