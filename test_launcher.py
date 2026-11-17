"""Tests for v1.3 tweet -> Pump.fun launcher. Run: python3 test_launcher.py
Live test uses jack's first tweet (id 20, public, no media) to verify the
FxTwitter mirror works. No token is ever launched in these tests — the
create_token test runs in dry-run mode only.
"""
import asyncio
import sys

sys.path.insert(0, ".")
import launcher
import twitter_fetch as tw


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(1)


async def main():
    # --- URL parsing ---
    h, tid = tw.parse_tweet_url("https://x.com/someone/status/1234567890123456789")
    check("parse x.com", h == "someone" and tid == "1234567890123456789")
    h, tid = tw.parse_tweet_url("https://twitter.com/elonmusk/status/999?ref_src=twsrc")
    check("parse twitter.com + query", h == "elonmusk" and tid == "999")
    h, tid = tw.parse_tweet_url("https://x.com/i/status/42")
    check("parse /i/status", tid == "42")
    check("parse invalid rejected", tw.parse_tweet_url("https://x.com/home") == (None, None))

    # --- FxTwitter JSON parsing (mock v1 + v2 shapes) ---
    v1 = {"code": 200, "tweet": {"text": "My cat Mini stole my samosa 😂",
                                 "author": {"screen_name": "riya", "name": "Riya"},
                                 "media": {"photos": [{"url": "https://pbs.twimg.com/media/abc.jpg"}]}}}
    out = tw._parse_fx(v1, "https://x.com/riya/status/1")
    check("parse v1 media", out["image_url"] == "https://pbs.twimg.com/media/abc.jpg"
          and out["handle"] == "riya")
    v2 = {"code": 200, "status": {"text": "gm", "author": {"screen_name": "x"},
                                  "media": {"all": [{"type": "photo", "url": "https://img/2.png"}]}}}
    out2 = tw._parse_fx(v2, "https://x.com/x/status/2")
    check("parse v2 media.all", out2["image_url"] == "https://img/2.png")
    check("parse no-media -> None",
          tw._parse_fx({"tweet": {"text": "hi", "author": {}}}, "https://x.com/a/status/3")["image_url"] is None)
    vx = {"text": "gm frens", "tweetID": "123", "user_name": "Bob",
          "user_screen_name": "bob",
          "media_extended": [{"type": "photo", "url": "https://pbs.twimg.com/media/z.jpg"}]}
    out3 = tw._parse_fx(vx, "https://x.com/bob/status/123")
    check("parse vxTwitter bare shape", out3["handle"] == "bob"
          and out3["image_url"] == "https://pbs.twimg.com/media/z.jpg")

    # --- name/symbol derivation ---
    m = tw.derive_token_meta("My cat Mini stole my samosa and ran away", "riya")
    check("derive name from text", m["name"] == "Cat Mini Stole Samosa")
    check("derive symbol", m["symbol"] == "CAT")
    check("derive disclaimer in description", "Not affiliated" in m["description"])
    m2 = tw.derive_token_meta("", "elonmusk")
    check("derive fallback", m2["name"] == "Elonmusk Post" and m2["symbol"] == "ELONMUSK")
    check("sanitize symbol", tw.sanitize_symbol("do-ge!") == "DOGE")
    try:
        tw.sanitize_name("x")
        check("sanitize short name rejected", False)
    except ValueError:
        check("sanitize short name rejected", True)

    # --- PumpPortal payload shape ---
    p = launcher.build_create_payload("Wallet111111111111111111111111111111111111",
                                      "Mint1111111111111111111111111111111111111",
                                      "Test Token", "TEST", "https://ipfs.io/ipfs/abc", 0.1)
    check("payload action create", p["action"] == "create"
          and p["tokenMetadata"]["uri"] == "https://ipfs.io/ipfs/abc"
          and p["amount"] == 0.1 and p["denominatedInSol"] == "true"
          and p["pool"] == "pump")

    # --- dry-run create: no network, no launch ---
    r = await launcher.create_token("Test Token", "TEST", "desc",
                                    "https://x.com/a/status/1", b"\xff\xd8fake",
                                    0.1, dry_run=True)
    check("dry-run create returns preview", r.get("dry_run") is True
          and r["name"] == "Test Token" and "mint" not in r)
    try:
        await launcher.create_token("T", "T", "d", "https://x.com/a/status/1",
                                    b"x", 5.0, dry_run=True)
        check("dry-run rejects >1 SOL dev buy", False)
    except ValueError:
        check("dry-run rejects >1 SOL dev buy", True)

    # --- live FxTwitter fetch (jack's first tweet, public, no media) ---
    live = await tw.fetch_tweet("https://x.com/jack/status/20")
    check("live fetch jack/status/20", live["text"] == "just setting up my twttr"
          and live["handle"] == "jack" and live["image_url"] is None)
    lm = tw.derive_token_meta(live["text"], live["handle"])
    check("live derive", lm["name"] == "Setting Up Twttr" and lm["symbol"] == "SETTING")

    print("\nAll launcher tests passed. No token was launched.")


if __name__ == "__main__":
    asyncio.run(main())
