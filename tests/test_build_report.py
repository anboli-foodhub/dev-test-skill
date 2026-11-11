"""Run: python3 -m unittest discover -s tests"""
import copy
import json
import os
import sys
import tempfile
import unittest
import zipfile
from xml.dom import minidom

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "dev-test")
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import build_report  # noqa: E402

with open(os.path.join(SKILL, "templates", "results.example.json"), encoding="utf-8") as f:
    EXAMPLE = json.load(f)


def case(data, tid):
    return next(tc for tc in data["test_cases"] if tc["id"] == tid)


class ValidateTest(unittest.TestCase):
    def test_example_is_valid(self):
        errors, _ = build_report.validate(EXAMPLE)
        self.assertEqual(errors, [])

    def test_pass_without_evidence_is_rejected(self):
        data = copy.deepcopy(EXAMPLE)
        case(data, "TC-001")["evidence"] = []
        errors, _ = build_report.validate(data)
        self.assertIn("TC-001: PASS without evidence", errors)

    def test_blocked_needs_reason(self):
        data = copy.deepcopy(EXAMPLE)
        case(data, "TC-004")["notes"] = ""
        errors, _ = build_report.validate(data)
        self.assertIn("TC-004: BLOCKED needs a reason in notes", errors)

    def test_verdict_cannot_be_better_than_results(self):
        data = copy.deepcopy(EXAMPLE)
        data["verdict"] = "READY"
        errors, _ = build_report.validate(data)
        self.assertTrue(any("better than the results allow" in e for e in errors))

    def test_blocker_defect_forces_not_ready(self):
        data = copy.deepcopy(EXAMPLE)
        data["defects"][0]["severity"] = "Blocker"
        self.assertEqual(build_report.lowest_allowed_verdict(data)[0], "NOT READY")

    def test_uncovered_ac_forces_not_ready(self):
        data = copy.deepcopy(EXAMPLE)
        data["acceptance_criteria"].append({"id": "AC-9", "text": "Untested"})
        self.assertEqual(build_report.lowest_allowed_verdict(data)[0], "NOT READY")

    def test_coverage_statuses(self):
        cov = build_report.ac_coverage(EXAMPLE)
        self.assertEqual(cov["AC-1"], (["TC-001", "TC-002"], "FAIL"))
        self.assertEqual(cov["AC-2"], (["TC-003"], "PASS"))


class BuildTest(unittest.TestCase):
    def test_writes_valid_workbook_and_files(self):
        data = copy.deepcopy(EXAMPLE)
        case(data, "TC-001")["notes"] = "control \x07 char & <tag> £ émoji 🙂"
        with tempfile.TemporaryDirectory() as out:
            xlsx = build_report.build(data, out)
            self.assertTrue(os.path.exists(os.path.join(out, "test-cases.csv")))
            self.assertTrue(os.path.exists(os.path.join(out, "summary.md")))
            with zipfile.ZipFile(xlsx) as z:
                self.assertIsNone(z.testzip())
                for name in z.namelist():
                    if name.endswith(".xml") or name.endswith(".rels"):
                        minidom.parseString(z.read(name))  # raises if not well-formed
                sheet = z.read("xl/worksheets/sheet2.xml").decode()
                self.assertIn("TC-004", sheet)
                self.assertIn("&lt;tag&gt;", sheet)


if __name__ == "__main__":
    unittest.main()
