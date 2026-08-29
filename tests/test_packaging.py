"""
Packaging and asset distribution verification tests for GridMind.
Verifies that dashboard HTML, CSS, and JS static assets are properly included in distributions.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


class TestPackaging(unittest.TestCase):
    """Verifies that built wheel/sdist distributions contain all dashboard assets."""

    def test_01_dashboard_assets_exist_in_source_tree(self) -> None:
        """Verifies template and static asset files physically exist in the project tree."""
        root = Path(__file__).resolve().parent.parent
        index_html = root / "dashboard" / "templates" / "index.html"
        dashboard_css = root / "dashboard" / "static" / "css" / "dashboard.css"
        dashboard_js = root / "dashboard" / "static" / "js" / "dashboard.js"

        self.assertTrue(index_html.exists(), f"Missing {index_html}")
        self.assertTrue(dashboard_css.exists(), f"Missing {dashboard_css}")
        self.assertTrue(dashboard_js.exists(), f"Missing {dashboard_js}")
        self.assertGreater(index_html.stat().st_size, 0)
        self.assertGreater(dashboard_css.stat().st_size, 0)
        self.assertGreater(dashboard_js.stat().st_size, 0)

    def test_02_built_wheel_includes_all_dashboard_assets(self) -> None:
        """Builds a wheel in a temporary directory and verifies asset inclusion."""
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp_dist:
            cmd = [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "-w",
                tmp_dist,
                str(root),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"pip wheel failed: {res.stderr}")

            wheel_files = [f for f in os.listdir(tmp_dist) if f.endswith(".whl")]
            self.assertGreater(len(wheel_files), 0, "No wheel built in output directory")
            wheel_path = os.path.join(tmp_dist, wheel_files[0])

            with zipfile.ZipFile(wheel_path, "r") as zf:
                namelist = zf.namelist()
                self.assertTrue(
                    any("dashboard/templates/index.html" in name for name in namelist),
                    f"dashboard/templates/index.html missing from wheel: {namelist}",
                )
                self.assertTrue(
                    any("dashboard/static/css/dashboard.css" in name for name in namelist),
                    f"dashboard/static/css/dashboard.css missing from wheel: {namelist}",
                )
                self.assertTrue(
                    any("dashboard/static/js/dashboard.js" in name for name in namelist),
                    f"dashboard/static/js/dashboard.js missing from wheel: {namelist}",
                )


if __name__ == "__main__":
    unittest.main()
