from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from llm_api_proof.reporting import compression_stats, read_json_report, write_json_report


class ReportingTests(unittest.TestCase):
    def test_gzip_json_report_round_trips_and_compresses(self) -> None:
        payload = {
            "workflow": "backend-tests-and-web-build",
            "status": "passed",
            "checks": [
                {
                    "name": "backend unit tests",
                    "status": "passed",
                    "command": "python -m unittest",
                    "details": ["repeat", "repeat", "repeat", "repeat"],
                },
                {
                    "name": "web production build",
                    "status": "passed",
                    "command": "npm run build",
                    "details": ["repeat", "repeat", "repeat", "repeat"],
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json.gz"
            write_json_report(report_path, payload, pretty=True)
            restored = read_json_report(report_path)
            stats = compression_stats(payload)

            self.assertEqual(restored, payload)
            self.assertTrue(report_path.exists())
            self.assertLess(stats["gzip_bytes"], stats["pretty_bytes"])


if __name__ == "__main__":
    unittest.main()
