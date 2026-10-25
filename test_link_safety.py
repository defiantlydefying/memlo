"""Deterministic download/DNS/redirect tests. Never makes a network request."""

import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import link_reader as links


class Response:
    def __init__(self, data=b"Study notes", *, status=200, ctype="text/plain", headers=None,
                 chunks=None, advertised_size=None):
        self.status = status
        self.content_type = ctype
        self.headers = headers or {}
        self.content_length = advertised_size
        self.parts = chunks if chunks is not None else [data]
        self.content = NS(iter_chunked=self.iter_chunks)

    async def iter_chunks(self, size):
        for part in self.parts:
            yield part

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class LinkSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def fetch(self, url, responses):
        session = Session(responses)
        resolver = NS(close=AsyncMock())
        with patch.object(links, "PublicResolver", return_value=resolver), \
                patch.object(links.aiohttp, "TCPConnector"), \
                patch.object(links.aiohttp, "ClientSession", return_value=session):
            result = await links.fetch_link(url)
        resolver.close.assert_awaited_once()
        self.assertTrue(all(not kwargs["allow_redirects"] for _, kwargs in session.requests))
        return result, session

    def test_url_guard_handles_malformed_credentials_and_local_addresses(self):
        for url in ("https://[", "https://example.com:bad/path", "https://user:secret@example.com/",
                    "http://127.0.0.1/", "http://[::1]/", "http://[::ffff:127.0.0.1]/",
                    "http://localhost./", "http://printer/", "http://10.0.0.1/",
                    "file:///etc/passwd", "https://example.com/\nsecret", None):
            with self.subTest(url=url):
                self.assertFalse(links._safe_url(url))
        self.assertTrue(links._safe_url("https://example.com/notes?chapter=2"))

    def test_non_public_and_multicast_addresses_are_rejected(self):
        for host in ("100.64.0.1", "0.0.0.0", "224.0.0.1", "ff02::1", "::ffff:10.0.0.1"):
            with self.subTest(host=host):
                self.assertTrue(links._is_blocked_host(host))

    def test_google_rewriting_requires_real_google_host(self):
        for url in ("https://evil.example/docs.google.com/document/d/ID/edit",
                    "https://docs.google.com.evil.example/document/d/ID/edit",
                    "https://example.com/?next=docs.google.com/document/d/ID/edit"):
            self.assertIsNone(links._google_export(url))
        self.assertEqual(links._google_export("https://docs.google.com/document/u/0/d/ABC/edit"),
                         ("https://docs.google.com/document/d/ABC/export?format=txt", "text"))

    async def test_bad_url_never_opens_session(self):
        with patch.object(links.aiohttp, "ClientSession") as session:
            result = await links.fetch_link("https://[")
        session.assert_not_called()
        self.assertEqual(result["error"], "blocked")

    async def test_dns_answers_are_checked_at_connection_resolution(self):
        underlying = NS(resolve=AsyncMock(return_value=[dict(host="127.0.0.1")]), close=AsyncMock())
        with patch.object(links, "DefaultResolver", return_value=underlying):
            resolver = links.PublicResolver()
        with self.assertRaises(links.BlockedLink):
            await resolver.resolve("public-looking.example", 443)
        underlying.resolve.return_value = [dict(host="8.8.8.8"), dict(host="10.0.0.1")]
        with self.assertRaises(links.BlockedLink):
            await resolver.resolve("mixed.example", 443)
        underlying.resolve.return_value = [dict(host="8.8.8.8")]
        self.assertEqual(await resolver.resolve("public.example", 443), [dict(host="8.8.8.8")])
        await resolver.close()
        underlying.close.assert_awaited_once()

    async def test_private_redirect_is_blocked_before_second_request(self):
        result, session = await self.fetch("https://example.com/notes", [
            Response(status=302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})])
        self.assertEqual(result["error"], "blocked")
        self.assertEqual(len(session.requests), 1)

    async def test_relative_public_redirect_is_followed(self):
        result, session = await self.fetch("https://example.com/old", [
            Response(status=302, headers={"Location": "/notes.txt"}), Response(b"Chemistry notes")])
        self.assertEqual(result["data"], b"Chemistry notes")
        self.assertEqual(session.requests[1][0], "https://example.com/notes.txt")

    async def test_redirect_limit_and_missing_location(self):
        result, _ = await self.fetch("https://example.com/notes", [
            Response(status=302, headers={"Location": "/again"}) for _ in range(links.MAX_REDIRECTS + 1)])
        self.assertEqual(result["error"], "too_many_redirects")
        result, _ = await self.fetch("https://example.com/notes", [Response(status=302)])
        self.assertEqual(result["error"], "invalid_redirect")

    async def test_size_limit_never_silently_truncates_file(self):
        with patch.object(links, "MAX_FETCH_BYTES", 4):
            with self.assertRaises(links.LinkTooLarge):
                await links._read_capped(Response(chunks=[b"abc", b"de"]))
            self.assertEqual(await links._read_capped(Response(chunks=[b"ab", b"cd"])), b"abcd")
            with self.assertRaises(links.LinkTooLarge):
                await links._read_capped(Response(advertised_size=5))
            result, _ = await self.fetch("https://example.com/notes", [Response(b"large")])
            self.assertEqual(result["error"], "too_large")

    async def test_private_google_document_and_empty_body(self):
        result, _ = await self.fetch("https://docs.google.com/document/d/ABC/edit", [Response(status=403)])
        self.assertEqual(result["error"], "private")
        result, _ = await self.fetch("https://example.com/notes", [Response(b"")])
        self.assertEqual(result["error"], "empty")

    async def test_fetch_errors_do_not_echo_credentials_or_urls(self):
        result, _ = await self.fetch("https://example.com/notes", [ValueError("sensitive text")])
        self.assertEqual(result["error"], "fetch_failed")


if __name__ == "__main__":
    unittest.main()
