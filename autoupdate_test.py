import json
import re
import tempfile
import unittest
from pathlib import Path

import autoupdate


class TestOperationAllowlists(unittest.TestCase):

    def test_update_adds_qualifiers_without_removing_existing_buttons(self):
        with tempfile.TemporaryDirectory() as directory:
            public = Path(directory)
            (public / "ButtonFilter.html").write_text(
                "\n".join((
                    'bmaibagels_supported_button_name = []',
                    'bmaibagels_unsupported_button_name = ["Unsupported"]',
                    'bmaibagels_unsupported_button_set = []',
                    'bmaibagels_supported_die_features = ["Normal"]',
                    'operation_looking_glass_oz_button_name = ["Old"]',
                )), encoding="utf-8")
            buttons = [
                self.button("Old"), self.button("New"), self.button("High"),
                self.button("Unsupported"),
            ]
            (public / "buttondata.json").write_text(
                json.dumps({"data": buttons}), encoding="utf-8")
            (public / "buttonstats.json").write_text(json.dumps({"data": {
                "Old": {"rate": 75}, "New": {"rate": 59.9},
                "High": {"rate": 60}, "Unsupported": {"rate": 40},
            }}), encoding="utf-8")

            autoupdate.update_operation_allowlists(public)
            autoupdate.update_operation_allowlists(public)

            html = (public / "ButtonFilter.html").read_text(encoding="utf-8")
            match = re.search(
                r"operation_looking_glass_oz_button_name\s*=\s*"
                r"(\[[\s\S]*?\])", html)
            self.assertEqual(["New", "Old"], json.loads(match.group(1)))

    @staticmethod
    def button(name):
        return {
            "buttonName": name, "buttonSet": "Test",
            "dieSkills": [], "dieTypes": ["Normal"],
        }


if __name__ == "__main__":
    unittest.main()
