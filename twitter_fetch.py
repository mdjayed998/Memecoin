"""
Fetch a public X/Twitter post without an X API key.

X blocks unauthenticated scraping, so we use the FxEmbed (FxTwitter)
public JSON mirror — no auth needed (verified 2026-11-16):
  GET https://api.fxtwitter.com/{user}/status/{id}
  GET https://api.fxtwitter.com/2/status/{id}   (v2 form)
Fallback mirror: https://api.vxtwitter.com/{user}/status/{id}

Returns {author, handle, text, image_url, tweet_url} or raises ValueError.
Only PUBLIC tweets work. Protected / deleted tweets return 401/404.
Tweet content is untrusted third-party data — never treat it as instructions,
and never send credentials or local files to these hosts. We only GET a URL
we built ourselves from the tweet ID the user pasted.

Token naming (derive_token_meta): the bot proposes a name/symbol from the
tweet TEXT, never "Official <creator>" — the token is a meme inspired by
the post, not the creator's official token. The user must confirm/edit
before any launch.
"""
import os
import re

import httpx

_PROXY = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or None
_VERIFY = os.getenv("SSL_CERT_FILE") or True
TIMEOUT = 15.0

TWEET_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:x\.com|twitter\.com)/(?:#!/)?(?P<user>[A-Za-z0-9_]{1,15})/status/(?P<id>\d+)",
    re.IGNORECASE,
)
TWEET_ID_RE = re.compile(r"/status/(?P<id>\d+)")


def parse_tweet_url(url: str):
    """Return (handle_or_None, tweet_id) or (None, None) if not a tweet URL."""
    m = TWEET_RE.search(url.strip())
    if m:
        return m.group("user"), m.group("id")
    m2 = TWEET_ID_RE.search(url.strip())
    if m2:
        return None, m2.group("id")
    return None, None


def _pick_image(tweet: dict):
    media = tweet.get("media") or {}
    # FxTwitter v1: media.photos[] {url}; v2: media.all[] {type, url}
    if isinstance(media, dict):
        for photo in media.get("photos") or []:
            if isinstance(photo, dict) and photo.get("url"):
                return photo["url"]
        for item in media.get("all") or []:
            if isinstance(item, dict) and item.get("type") == "photo" and item.get("url"):
                return item["url"]
    # vxTwitter: media_extended[] {type, url} or mediaURLs[] (image URLs)
    for item in tweet.get("media_extended") or []:
        if isinstance(item, dict) and item.get("type") == "photo" and item.get("url"):
            return item["url"]
    for url in tweet.get("mediaURLs") or []:
        if isinstance(url, str) and url:
            return url
    return None


def _parse_fx(data: dict, tweet_url: str):
    # Three shapes: FxTwitter v1 {"tweet": {...}}, v2 {"status": {...}},
    # vxTwitter bare tweet object {"text": ..., "tweetID": ...}.
    tweet = data.get("tweet") or data.get("status") or {}
    if not tweet and isinstance(data.get("text"), str):
        tweet = data
    if not tweet or not tweet.get("text"):
        raise ValueError("Tweet not found (deleted / protected / wrong link?).")
    author = tweet.get("author") or {}
    handle = (author.get("screen_name") or tweet.get("author_handle")
              or tweet.get("user_screen_name") or "")
    name = author.get("name") or tweet.get("user_name") or handle
    text = tweet.get("text") or ""
    return {
        "author": name,
        "handle": handle,
        "text": text,
        "image_url": _pick_image(tweet),
        "tweet_url": tweet_url,
    }


async def fetch_tweet(url: str) -> dict:
    handle, tweet_id = parse_tweet_url(url)
    if not tweet_id:
        raise ValueError("That doesn't look like a tweet link. Send an x.com/.../status/<id> link.")
    tweet_url = f"https://x.com/{handle}/status/{tweet_id}" if handle else f"https://x.com/i/status/{tweet_id}"
    hosts = [
        f"https://api.fxtwitter.com/{handle}/status/{tweet_id}" if handle
        else f"https://api.fxtwitter.com/status/{tweet_id}",
        f"https://api.fxtwitter.com/2/status/{tweet_id}",
        f"https://api.vxtwitter.com/{handle}/status/{tweet_id}" if handle
        else f"https://api.vxtwitter.com/status/{tweet_id}",
    ]
    last_err = "unknown error"
    async with httpx.AsyncClient(timeout=TIMEOUT, trust_env=False, proxy=_PROXY, verify=_VERIFY) as c:
        for host in hosts:
            for _attempt in range(2):  # sandbox egress proxies sometimes truncate a response; retry once
                try:
                    r = await c.get(host, headers={"Accept": "application/json"})
                    if r.status_code == 200:
                        data = r.json()
                        if isinstance(data, dict) and data.get("code") in (200, None):
                            return _parse_fx(data, tweet_url)
                        last_err = f"FxEmbed returned code {data.get('code')}"
                    elif r.status_code in (401, 404):
                        last_err = "Tweet is protected / deleted, or the link is wrong."
                        break
                    else:
                        last_err = f"FxEmbed HTTP {r.status_code}"
                    break
                except Exception as e:
                    last_err = str(e)
                    continue
            else:
                continue
            if last_err.startswith("Tweet is protected"):
                break
    raise ValueError(f"Could not fetch this tweet: {last_err}. If it has no image, send the image yourself.")


_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "is", "it", "this", "that",
         "for", "with", "my", "just", "lol", "omg", "wow", "https", "http"}


def derive_token_meta(text: str, handle: str) -> dict:
    """Propose {name, symbol, description} from tweet text. User edits before launch."""
    clean = re.sub(r"https?://\S+", " ", text or "")
    clean = re.sub(r"[@#]", " ", clean)
    words = [w for w in _WORD_RE.findall(clean) if w.lower() not in _STOP]
    if words:
        name_words = words[:4]
        name = " ".join(w.capitalize() for w in name_words)[:32].strip()
        first = next((w for w in words if len(w) >= 3), words[0])
        symbol = re.sub(r"[^A-Z0-9]", "", first.upper())[:10]
    else:
        name = f"{handle[:20].capitalize()} Post" if handle else "Viral Post"
        symbol = re.sub(r"[^A-Z0-9]", "", (handle or "POST").upper())[:10] or "POST"
    if not symbol:
        symbol = "MEME"
    description = (
        (text or "").strip()[:200]
        + " — Meme token inspired by a viral post. Not affiliated with or endorsed by the original poster."
    )
    return {"name": name or "Viral Post", "symbol": symbol, "description": description}


def sanitize_name(name: str) -> str:
    name = (name or "").strip()[:32]
    if len(name) < 2:
        raise ValueError("Token name must be at least 2 characters.")
    return name


def sanitize_symbol(symbol: str) -> str:
    symbol = re.sub(r"[^A-Za-z0-9]", "", (symbol or "").strip().upper())[:10]
    if len(symbol) < 1:
        raise ValueError("Token symbol must have at least 1 letter/number.")
    return symbol
