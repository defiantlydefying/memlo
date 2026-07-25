"""Offline checks for the link-reading helpers — no network, no API key needed.

  python3 test_links.py

Only the pure functions are exercised here (URL parsing, Google-link rewriting,
HTML-to-text, the host guard). fetch_link and looks_like_study_material need the
network and Claude, so they're covered by actually posting a link to the bot.
"""
import os

os.environ.setdefault("ANTHROPIC_API_KEY", "not-needed-for-these-tests")
import bot

passed = failed = 0


def check(label, got, want):
    global passed, failed
    ok = got == want
    passed += ok
    failed += not ok
    print(f'  {"ok " if ok else "FAIL"} {label}')
    if not ok:
        print(f"        got:  {got!r}\n        want: {want!r}")


print("=== extract_urls ===")
check("plain url",
      bot.extract_urls("see https://example.com/rubric for the rubric"),
      ["https://example.com/rubric"])
check("trailing period trimmed",
      bot.extract_urls("it's at https://docs.google.com/document/d/abc123/edit."),
      ["https://docs.google.com/document/d/abc123/edit"])
check("discord <embed-suppressed> brackets excluded",
      bot.extract_urls("<https://example.com/x>"),
      ["https://example.com/x"])
check("de-duplicates",
      bot.extract_urls("https://a.com https://a.com"),
      ["https://a.com"])
check("caps at MAX_LINKS_PER_MESSAGE",
      len(bot.extract_urls(" ".join(f"https://a.com/{i}" for i in range(20)))),
      bot.MAX_LINKS_PER_MESSAGE)
check("no urls -> empty", bot.extract_urls("no links here"), [])

print("\n=== _google_export ===")
check("doc -> txt export",
      bot._google_export("https://docs.google.com/document/d/ABC-1_2/edit?usp=sharing"),
      ("https://docs.google.com/document/d/ABC-1_2/export?format=txt", "text"))
check("sheet -> csv export",
      bot._google_export("https://docs.google.com/spreadsheets/d/XYZ/edit#gid=0"),
      ("https://docs.google.com/spreadsheets/d/XYZ/export?format=csv", "text"))
check("slides -> pdf export",
      bot._google_export("https://docs.google.com/presentation/d/PPP/edit"),
      ("https://docs.google.com/presentation/d/PPP/export/pdf", "pdf"))
check("drive file -> download",
      bot._google_export("https://drive.google.com/file/d/FID/view?usp=sharing"),
      ("https://drive.google.com/uc?export=download&id=FID", "auto"))
check("non-google -> None",
      bot._google_export("https://example.com/notes.pdf"), None)

print("\n=== _is_blocked_host ===")
for host in ("localhost", "127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.1.1",
             "printer.local", "[::1]", ""):
    check(f"blocks {host!r}", bot._is_blocked_host(host), True)
for host in ("example.com", "docs.google.com", "8.8.8.8"):
    check(f"allows {host!r}", bot._is_blocked_host(host), False)

print("\n=== _meta / _html_to_text ===")
html_doc = b"""<html><head><title>Photosynthesis - Khan Academy</title>
<meta name="description" content="How plants make food from light.">
<meta property="og:title" content="Photosynthesis explained">
</head><body><script>var x=1;</script><h1>Light reactions</h1>
<p>Chlorophyll absorbs light &amp; splits water.</p></body></html>"""
check("_meta reads description",
      bot._meta(html_doc.decode(), "description"), "How plants make food from light.")
title, text = bot._html_to_text(html_doc)
check("title parsed", title, "Photosynthesis - Khan Academy")
check("script stripped", "var x=1" not in text, True)
check("entities unescaped", "splits water" in text and "&amp;" not in text, True)
check("meta description folded in", "make food from light" in text, True)

print("\n=== _label_from_url ===")
check("last path segment", bot._label_from_url("https://site.edu/handouts/ch3.pdf"), "ch3.pdf")
check("falls back to host", bot._label_from_url("https://site.edu/"), "site.edu")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
