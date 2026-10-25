"""Study-link parsing and downloads, independent of Discord and model clients."""

import asyncio
import html
import ipaddress
import re
from urllib.parse import urlparse, unquote, urljoin

import aiohttp
from aiohttp.abc import AbstractResolver
from aiohttp.resolver import DefaultResolver

CLAUDE_IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")

# --- Reading links people drop -------------------------------------
# A shared link is study material too: a Google Doc, a rubric on the class site, a
# PDF, an educational video. We fetch whatever's linked, let Haiku judge whether it
# was actually worth studying (vs a meme or a game invite), and file the keepers the
# same way an uploaded file is filed. See fetch_link / read_links below.
FETCH_TIMEOUT = 15                    # seconds to wait on a single link
MAX_FETCH_BYTES = 10 * 1024 * 1024    # stop reading a link after 10 MB
MAX_LINKS_PER_MESSAGE = 5             # don't let one message fan out to dozens of fetches
MAX_PAGE_CHARS = 20000               # how much of a web page's text we hand to Claude
MAX_REDIRECTS = 5
# A real browser User-Agent: some sites answer a bare Python client with 403.
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
# URLs inside message text. Stops at whitespace and the brackets Discord/markdown wrap
# links in; trailing sentence punctuation is trimmed off separately in extract_urls.
URL_RE = re.compile(r'https?://[^\s<>()\[\]{}"\'|\\^`]+', re.IGNORECASE)

# --- Reading a link's contents (Google Docs, PDFs, web pages) -------
# The goal is narrow on purpose: not "browse the web", but "someone dropped a study
# material as a link instead of a file". So we fetch the one URL, turn it into the same
# text/PDF/image bytes that ingest_attachment already knows how to read, and reuse that
# whole path. Everything Claude-facing is unchanged; this is just a new way in.

def extract_urls(text):
    """Every http(s) URL in a message, de-duplicated, capped, with trailing sentence
    punctuation trimmed ("...see docs.google.com/x." shouldn't fetch a URL ending in
    a period). Discord's <link> embed-suppression brackets are excluded by URL_RE."""
    urls = []
    for raw in URL_RE.findall(text or ""):
        url = raw.rstrip('.,;:!?)]}\'"')
        if url not in urls:
            urls.append(url)
    return urls[:MAX_LINKS_PER_MESSAGE]


def _is_blocked_host(host):
    """Only public internet destinations belong in the study-link reader."""
    host = (host or "").lower().strip("[]").rstrip(".")
    if not host or host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host   # disallow local, single-label network names
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return not ip.is_global or ip.is_multicast


class BlockedLink(ValueError):
    pass


class LinkTooLarge(ValueError):
    pass


class PublicResolver(AbstractResolver):
    """Validate the exact DNS answers aiohttp will connect to (no check/use gap)."""

    def __init__(self):
        self.resolver = DefaultResolver()

    async def resolve(self, host, port=0, family=0):
        if _is_blocked_host(host):
            raise BlockedLink("Non-public hostname")
        answers = await self.resolver.resolve(host, port, family)
        if not answers or any(_is_blocked_host(answer["host"]) for answer in answers):
            raise BlockedLink("DNS returned a non-public address")
        return answers

    async def close(self):
        await self.resolver.close()


def _safe_url(url):
    try:
        parsed = urlparse(url)
        if re.search(r"[\x00-\x20\x7f\\]", url) or parsed.username is not None:
            return False
        # Accessing .port also validates malformed/non-numeric port strings.
        port = parsed.port
        return (parsed.scheme in ("http", "https") and not _is_blocked_host(parsed.hostname)
                and (port is None or 0 < port <= 65535))
    except (ValueError, TypeError):
        return False


def _google_export(url):
    """Turn a Google Docs/Slides/Sheets/Drive share link into a direct-download URL,
    since the human-facing /edit page is a JavaScript app with none of the content in
    the HTML. Returns (download_url, kind) where kind is 'text', 'pdf', or 'auto'
    (let the response's content-type decide), or None if it isn't a Google link.

    Requires the doc to be link-shared ('Anyone with the link'); a private doc answers
    the export URL with an HTML sign-in page, which fetch_link detects and reports."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
    except ValueError:
        return None
    if host == "docs.google.com":
        for kind, suffix, media in (("document", "/export?format=txt", "text"),
                                    ("spreadsheets", "/export?format=csv", "text"),
                                    ("presentation", "/export/pdf", "pdf")):
            match = re.match(rf"/{kind}/(?:u/\d+/)?d/([\w-]+)(?:/|$)", parsed.path)
            if match:
                return f"https://docs.google.com/{kind}/d/{match.group(1)}{suffix}", media
    if host == "drive.google.com":
        match = re.match(r"/file/d/([\w-]+)(?:/|$)", parsed.path)
        if match:
            return f"https://drive.google.com/uc?export=download&id={match.group(1)}", "auto"
    return None


def _meta(html_str, key):
    """A <meta property/name="key"> tag's content, matched without assuming attribute
    order (real pages put content before property as often as after)."""
    for tag in re.findall(r"<meta\b[^>]*>", html_str, re.I):
        if re.search(rf'(?:property|name)\s*=\s*["\']{re.escape(key)}["\']', tag, re.I):
            m = re.search(r'content\s*=\s*["\'](.*?)["\']', tag, re.I | re.S)
            if m:
                return html.unescape(m.group(1)).strip()
    return ""


def _html_to_text(raw_bytes):
    """A web page's readable text. Leads with <title> and the og:/meta description,
    because JavaScript-heavy pages (a YouTube video, say) carry their real subject
    there while the body is mostly script — so a bare tag-strip would miss it. Then the
    body with scripts/styles/tags removed. Returns (title, text)."""
    doc = raw_bytes.decode("utf-8", errors="ignore")
    m = re.search(r"<title[^>]*>(.*?)</title>", doc, re.I | re.S)
    title = html.unescape(re.sub(r"\s+", " ", m.group(1)).strip()) if m else ""
    lead = [t for t in (title, _meta(doc, "og:title"), _meta(doc, "og:description"),
                        _meta(doc, "description")) if t]
    body = re.sub(r"<(script|style|noscript)\b[^>]*>.*?</\1>", " ", doc, flags=re.I | re.S)
    body = html.unescape(re.sub(r"<[^>]+>", " ", body))
    body = re.sub(r"\s+", " ", body).strip()
    # De-dup: the title/description almost always reappear in the body verbatim.
    text = "\n".join(dict.fromkeys(lead + [body]))
    return title, text[:MAX_PAGE_CHARS]


def _label_from_url(url):
    """A short human name for a link when the page has no title: last path segment,
    else the hostname."""
    parsed = urlparse(url)
    tail = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    return (tail or parsed.hostname or url)[:80]


async def _read_capped(resp):
    """Reject oversized bodies—never pass a silently truncated PDF to the model."""
    if getattr(resp, "content_length", None) and resp.content_length > MAX_FETCH_BYTES:
        raise LinkTooLarge("Response exceeds the download limit")
    chunks, total = [], 0
    async for chunk in resp.content.iter_chunked(65536):
        total += len(chunk)
        if total > MAX_FETCH_BYTES:
            raise LinkTooLarge("Response exceeds the download limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _sniff_type(data):
    """The real media type of a downloaded body, read from its first bytes.

    Google Drive's download endpoint labels nearly everything application/octet-stream,
    so trusting the Content-Type header alone makes us reject PDFs and photos we can
    actually read. Returns a media type, or None if the bytes aren't anything we handle."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    # No magic number. If it decodes cleanly and reads as printable characters it's a
    # text file (someone's .txt/.md/.csv notes); otherwise it's a binary we can't use.
    head = data[:2048]
    if not head:
        return None
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        return None
    printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in text)
    return "text/plain" if printable / len(text) > 0.9 else None


async def fetch_link(url):
    """Fetch one URL and normalise it into what ingest_attachment expects. Returns a
    dict {label, media_type, data(bytes), is_text, snippet} on success, or {"error": ...}.
    'snippet' is a short text peek used only to judge whether it's study material.

    Never raises: every failure (blocked host, timeout, 404, a Google doc that isn't
    shared, a file type we can't read) comes back as an "error" the caller decides how
    loud to be about."""
    if not _safe_url(url):
        return {"error": "blocked"}

    fetch_from, forced = url, None
    google = _google_export(url)
    if google:
        fetch_from, forced = google

    resolver = PublicResolver()
    try:
        timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
        headers = {"User-Agent": BROWSER_UA}
        connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False)
        async with asyncio.timeout(FETCH_TIMEOUT), aiohttp.ClientSession(
                timeout=timeout, headers=headers, connector=connector) as session:
            for hop in range(MAX_REDIRECTS + 1):
                if not _safe_url(fetch_from):
                    return {"error": "blocked"}
                async with session.get(fetch_from, allow_redirects=False) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location")
                        if not location:
                            return {"error": "invalid_redirect"}
                        if hop == MAX_REDIRECTS:
                            return {"error": "too_many_redirects"}
                        fetch_from = urljoin(fetch_from, location)
                        continue
                    if resp.status != 200:
                        return {"error": "private" if forced and resp.status in (401, 403)
                                else f"http {resp.status}"}
                    ctype = (resp.content_type or "").lower()
                    disposition = resp.headers.get("Content-Disposition", "")
                    data = await _read_capped(resp)
                    break
    except BlockedLink:
        return {"error": "blocked"}
    except LinkTooLarge:
        return {"error": "too_large"}
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return {"error": "fetch_failed"}
    finally:
        await resolver.close()

    if not data:
        return {"error": "empty"}

    label = _label_from_url(url)

    # Every Drive share link ends in /view, so the URL alone names the material "view".
    # The download response carries the real filename — much better to remember it by.
    name = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition)
    if name:
        label = unquote(name.group(1)).strip()[:80] or label

    # A Google export that comes back as HTML is the "you need access" sign-in page —
    # the doc isn't link-shared. Worth telling the poster about; they can fix it.
    # Drive downloads ('auto') count too: a real file arrives as a PDF, an image, or
    # some other binary, never as a web page. HTML there means Google served an
    # interstitial instead of the file, and ingesting that would remember a sign-in
    # page as class notes.
    if forced and ctype.startswith("text/html"):
        return {"error": "private"}

    # A server that won't say what it sent (Drive answers octet-stream for most files)
    # leaves the bytes as the only evidence. Only override a header this uninformative —
    # a server that names a real type is more reliable than a guess.
    if ctype in ("", "application/octet-stream", "binary/octet-stream"):
        ctype = _sniff_type(data) or ctype

    is_html = ctype.startswith("text/html")
    is_pdf = ctype == "application/pdf" or (forced == "pdf")
    is_image = ctype.startswith("image/")
    is_text = (ctype.startswith("text/") and not is_html) or forced == "text"

    if is_html:
        title, text = _html_to_text(data)
        return {"label": (title or label)[:80], "media_type": None,
                "data": text.encode("utf-8"), "is_text": True, "snippet": text[:1500]}
    if is_pdf:
        return {"label": label, "media_type": "application/pdf",
                "data": data, "is_text": False, "snippet": "(a PDF document)"}
    if is_image:
        if ctype not in CLAUDE_IMAGE_TYPES:
            return {"error": f"image type {ctype}"}
        return {"label": label, "media_type": ctype,
                "data": data, "is_text": False, "snippet": "(an image)"}
    if is_text:
        text = data.decode("utf-8", errors="ignore")
        return {"label": label, "media_type": None,
                "data": data, "is_text": True, "snippet": text[:1500]}
    return {"error": f"unsupported type {ctype or '?'}"}
