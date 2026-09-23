"""Educational care content must never turn a research score into treatment."""
import json
import unittest
from urllib.parse import urlparse

from ecg_cvd.care import care_information
from ecg_cvd.gui import CLASS_NAMES


class CareInformationTests(unittest.TestCase):
    def test_all_seventeen_labels_have_json_safe_source_linked_information(self):
        self.assertEqual(len(CLASS_NAMES), 17)
        required = {"title", "disclaimer", "summary", "next_steps", "emergency", "sources", "reviewed_on"}
        for number, label in enumerate(CLASS_NAMES, 1):
            with self.subTest(label=label):
                info = care_information(f"{number} {label}")
                self.assertEqual(set(info), required)
                self.assertEqual(json.loads(json.dumps(info)), info)
                self.assertIn("not a treatment recommendation", info["title"])
                self.assertIn("not a diagnosis", info["disclaimer"])
                self.assertIn("clinician", info["disclaimer"])
                self.assertNotIn("no supported condition-specific", info["summary"])
                self.assertGreaterEqual(len(info["next_steps"]), 2)
                self.assertGreaterEqual(len(info["sources"]), 3)
                self.assertEqual(info["reviewed_on"], "2026-09-19")
                for source in info["sources"]:
                    self.assertTrue(source["title"])
                    url = urlparse(source["url"])
                    self.assertEqual(url.scheme, "https")
                    self.assertIn(url.hostname, {"www.heart.org", "www.nhlbi.nih.gov"})

    def test_unknown_empty_and_none_have_safe_fallback(self):
        for label in ("unrecognized", "", None, "999 other", "<script>alert(1)</script>"):
            with self.subTest(label=label):
                info = care_information(label)
                self.assertIn("no supported condition-specific", info["summary"])
                self.assertIn("not a diagnosis", info["disclaimer"])
                self.assertNotIn("<script>", json.dumps(info))

    def test_case_whitespace_and_numbered_labels_are_supported(self):
        for code in CLASS_NAMES:
            self.assertEqual(care_information(code), care_information(f" 17   {code.lower()}  "))

    def test_nsr_does_not_exclude_disease(self):
        info = care_information("1 NSR")
        self.assertIn("does not exclude heart disease", info["summary"])
        self.assertIn("skip an assessment", " ".join(info["next_steps"]))

    def test_high_risk_labels_are_conditional_and_do_not_diagnose_current_emergency(self):
        for label in ("10 VT", "12 VFL"):
            with self.subTest(label=label):
                info = care_information(label)
                self.assertIn("If", info["summary"])
                self.assertIn("confirmed in a person", info["summary"])
                self.assertIn("urgent medical assessment", info["summary"])
                self.assertIn("does not establish a current emergency", info["emergency"])
                self.assertIn("current chest pain", info["emergency"])

    def test_vfl_is_not_mislabeled_fibrillation(self):
        info = care_information("VFL")
        self.assertIn("ventricular flutter", info["summary"])
        self.assertIn("not the ventricular fibrillation", info["summary"])

    def test_caller_mutation_does_not_change_later_results(self):
        info = care_information("AFIB")
        info["next_steps"].clear()
        info["sources"][0]["url"] = "https://invalid.test"
        fresh = care_information("AFIB")
        self.assertTrue(fresh["next_steps"])
        self.assertNotEqual(fresh["sources"][0]["url"], "https://invalid.test")

    def test_content_has_no_drug_doses_or_claim_of_clinical_validation(self):
        for label in CLASS_NAMES:
            text = json.dumps(care_information(label))
            with self.subTest(label=label):
                self.assertNotRegex(text, r"\b\d+\s*(?:mg|mcg|ml)\b")
                self.assertIn("has not received independent clinical validation", text)


if __name__ == "__main__":
    unittest.main()
