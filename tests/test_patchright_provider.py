# -*- coding: utf-8 -*-
"""Unit tests for the Patchright search provider (offline, mocked HTTP)."""

import sys
import unittest
from unittest.mock import MagicMock, patch

# Mock newspaper before search_service import (optional dependency)
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

from src.search_service import (
    PatchrightSearchProvider,
    SearchResponse,
    SearchService,
)


class TestPatchrightSearchProvider(unittest.TestCase):
    def _provider(self, base_url="http://127.0.0.1:8931") -> PatchrightSearchProvider:
        return PatchrightSearchProvider(base_url)

    def test_is_available_with_base_url(self) -> None:
        self.assertTrue(self._provider().is_available)
        self.assertTrue(self._provider("http://localhost:9000").is_available)

    def test_is_available_without_base_url(self) -> None:
        provider = PatchrightSearchProvider("")
        self.assertFalse(provider.is_available)

    @patch("requests.post")
    def test_success_response_maps_fields(self, mock_post: MagicMock) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "query": "第一创业 002797 最新 新闻",
            "results": [
                {
                    "title": "第一创业新闻标题",
                    "snippet": "摘要内容",
                    "url": "https://example.com/news/1",
                    "source": "example.com",
                    "published_date": "2026-08-10",
                }
            ],
            "provider": "Patchright",
            "success": True,
            "error_message": None,
            "search_time": 1.2,
        }
        mock_post.return_value = resp

        provider = self._provider()
        response = provider.search("第一创业 002797 最新 新闻", max_results=5, days=3)

        self.assertTrue(response.success)
        self.assertEqual(response.provider, "Patchright")
        self.assertEqual(len(response.results), 1)
        result = response.results[0]
        self.assertEqual(result.title, "第一创业新闻标题")
        self.assertEqual(result.url, "https://example.com/news/1")
        self.assertEqual(result.source, "example.com")
        self.assertEqual(result.published_date, "2026-08-10")
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["max_results"], 5)
        self.assertEqual(kwargs["json"]["days"], 3)

    @patch("requests.post")
    def test_http_error_returns_failure(self, mock_post: MagicMock) -> None:
        resp = MagicMock()
        resp.status_code = 503
        mock_post.return_value = resp

        response = self._provider().search("query", max_results=5, days=3)
        self.assertFalse(response.success)
        self.assertEqual(response.error_message, "HTTP 503")
        self.assertEqual(response.results, [])

    @patch("requests.post")
    def test_request_exception_returns_failure(self, mock_post: MagicMock) -> None:
        import requests

        mock_post.side_effect = requests.exceptions.ConnectionError("refused")

        response = self._provider().search("query", max_results=5, days=3)
        self.assertFalse(response.success)
        self.assertIn("不可达", response.error_message or "")

    @patch("requests.post")
    def test_timeout_returns_failure(self, mock_post: MagicMock) -> None:
        import requests

        mock_post.side_effect = requests.exceptions.Timeout()

        response = self._provider().search("query", max_results=5, days=3)
        self.assertFalse(response.success)
        self.assertIn("超时", response.error_message or "")

    @patch("requests.post")
    def test_malformed_payload_degrades_gracefully(self, mock_post: MagicMock) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"results": [{"url": "https://a.com/x"}]}
        mock_post.return_value = resp

        response = self._provider().search("query", max_results=5, days=3)
        self.assertTrue(response.success)
        self.assertEqual(len(response.results), 1)
        self.assertEqual(response.results[0].title, "")


class TestPatchrightProviderWiring(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        service = SearchService(
            searxng_base_urls=[],
            searxng_public_instances_enabled=False,
        )
        self.assertFalse(any(p.name == "Patchright" for p in service._providers))

    def test_enabled_appends_provider(self) -> None:
        service = SearchService(
            searxng_base_urls=[],
            searxng_public_instances_enabled=False,
            patchright_enabled=True,
            patchright_base_url="http://127.0.0.1:8931",
        )
        names = [p.name for p in service._providers]
        self.assertIn("Patchright", names)
        # Patchright 默认排在 SearXNG 之后作兜底
        self.assertEqual(names[-1], "Patchright")

    def test_constructor_kwargs_carries_patchright_config(self) -> None:
        service = SearchService(
            patchright_enabled=True,
            patchright_base_url="http://127.0.0.1:9000",
        )
        kwargs = service._constructor_kwargs
        self.assertTrue(kwargs["patchright_enabled"])
        self.assertEqual(kwargs["patchright_base_url"], "http://127.0.0.1:9000")


if __name__ == "__main__":
    unittest.main()
