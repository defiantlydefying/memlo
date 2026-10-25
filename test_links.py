"""Offline checks for the link-reading helpers — no network, no API key needed.

  python3 test_links.py

Only the pure functions are exercised here (URL parsing, Google-link rewriting,
HTML-to-text, the host guard). fetch_link and looks_like_study_material need the
network and Claude, so they're covered by actually posting a link to the bot.
"""
import os
import unittest

os.environ.setdefault("ANTHROPIC_API_KEY", "not-needed-for-these-tests")
import link_reader as links

passed = failed = 0
checks = []


def check(label, got, want):
    global passed, failed
    checks.append((label, got, want))
    ok = got == want
    passed += ok
    failed += not ok
    if __name__ == "__main__":
        print(f'  {"ok " if ok else "FAIL"} {label}')
    if not ok and __name__ == "__main__":
        print(f"        got:  {got!r}\n        want: {want!r}")


if __name__ == "__main__":
    print("=== extract_urls ===")
check("plain url",
      links.extract_urls("see https://example.com/rubric for the rubric"),
      ["https://example.com/rubric"])
check("trailing period trimmed",
      links.extract_urls("it's at https://docs.google.com/document/d/abc123/edit."),
      ["https://docs.google.com/document/d/abc123/edit"])
check("discord <embed-suppressed> brackets excluded",
      links.extract_urls("<https://example.com/x>"),
      ["https://example.com/x"])
check("de-duplicates",
      links.extract_urls("https://a.com https://a.com"),
      ["https://a.com"])
check("caps at MAX_LINKS_PER_MESSAGE",
      len(links.extract_urls(" ".join(f"https://a.com/{i}" for i in range(20)))),
      links.MAX_LINKS_PER_MESSAGE)
check("no urls -> empty", links.extract_urls("no links here"), [])

if __name__ == "__main__":
    print("\n=== _google_export ===")
check("doc -> txt export",
      links._google_export("https://docs.google.com/document/d/ABC-1_2/edit?usp=sharing"),
      ("https://docs.google.com/document/d/ABC-1_2/export?format=txt", "text"))
check("sheet -> csv export",
      links._google_export("https://docs.google.com/spreadsheets/d/XYZ/edit#gid=0"),
      ("https://docs.google.com/spreadsheets/d/XYZ/export?format=csv", "text"))
check("slides -> pdf export",
      links._google_export("https://docs.google.com/presentation/d/PPP/edit"),
      ("https://docs.google.com/presentation/d/PPP/export/pdf", "pdf"))
check("drive file -> download",
      links._google_export("https://drive.google.com/file/d/FID/view?usp=sharing"),
      ("https://drive.google.com/uc?export=download&id=FID", "auto"))
check("non-google -> None",
      links._google_export("https://example.com/notes.pdf"), None)

if __name__ == "__main__":
    print("\n=== _is_blocked_host ===")
for host in ("localhost", "127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.1.1",
             "printer.local", "[::1]", ""):
    check(f"blocks {host!r}", links._is_blocked_host(host), True)
for host in ("example.com", "docs.google.com", "8.8.8.8"):
    check(f"allows {host!r}", links._is_blocked_host(host), False)

if __name__ == "__main__":
    print("\n=== _meta / _html_to_text ===")
html_doc = b"""<html><head><title>Photosynthesis - Khan Academy</title>
<meta name="description" content="How plants make food from light.">
<meta property="og:title" content="Photosynthesis explained">
</head><body><script>var x=1;</script><h1>Light reactions</h1>
<p>Chlorophyll absorbs light &amp; splits water.</p></body></html>"""
check("_meta reads description",
      links._meta(html_doc.decode(), "description"), "How plants make food from light.")
title, text = links._html_to_text(html_doc)
check("title parsed", title, "Photosynthesis - Khan Academy")
check("script stripped", "var x=1" not in text, True)
check("entities unescaped", "splits water" in text and "&amp;" not in text, True)
check("meta description folded in", "make food from light" in text, True)

if __name__ == "__main__":
    print("\n=== _label_from_url ===")
check("last path segment", links._label_from_url("https://site.edu/handouts/ch3.pdf"), "ch3.pdf")
check("falls back to host", links._label_from_url("https://site.edu/"), "site.edu")

class LinkTests(unittest.TestCase):
    def test_link_helpers(self):
        for label, got, want in checks:
            with self.subTest(label=label):
                self.assertEqual(got, want)


if __name__ == "__main__":
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
