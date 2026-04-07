import unittest

from agent.url_utils import attach_query, normalized_role_company


class TestUrlUtils(unittest.TestCase):
    def test_normalized_role_company(self) -> None:
        self.assertEqual(normalized_role_company("ACME Inc.", "Senior DevOps/Platform"), "acme inc::senior devops platform")

    def test_attach_query_param(self) -> None:
        out = attach_query("https://example.com/jobs", query_param="q", query="backend engineer")
        self.assertIn("q=backend+engineer", out)

