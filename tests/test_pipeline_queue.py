import unittest
from pathlib import Path
import tempfile

from cli.pipeline_queue import append_pending, ensure_pipeline_file, load_pipeline, save_pipeline, PipelineState


class TestPipelineQueue(unittest.TestCase):
    def test_round_trip_and_append(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = ensure_pipeline_file(root)
            state = load_pipeline(path)
            self.assertEqual(state.pending, [])
            self.assertEqual(state.processed, [])

            n = append_pending(root, ["https://a.com/1", "https://a.com/2", "https://a.com/1"])
            self.assertEqual(n, 2)

            state2 = load_pipeline(path)
            self.assertEqual(state2.pending, ["https://a.com/1", "https://a.com/2"])

            state2.processed = ["https://a.com/1"]
            save_pipeline(path, state2)
            state3 = load_pipeline(path)
            self.assertEqual(state3.processed, ["https://a.com/1"])

