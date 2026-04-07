import unittest
from pathlib import Path
import tempfile

from agent.scan_history import append_scan_history_row, parse_scan_history_keys


class TestScanHistory(unittest.TestCase):
    def test_append_and_parse_tsv(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            append_scan_history_row(root, "Portal", "Co", "Role", "https://x", "new")
            keys = parse_scan_history_keys(root)
            self.assertIn("https://x", keys.urls)
            self.assertTrue(any("co" in k for k in keys.role_company))

