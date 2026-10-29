import copy
import hashlib
import json
import pathlib
import unittest

from validate_package import (
    PKG_PATH,
    RECEIPT_PATH,
    SCHEMA_PATH,
    build_negative_fixtures,
    canonical_lineage_payload,
    validate_against_json_schema,
    validate_package,
    validate_receipt,
)


class TestWfbPackageValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.pkg = json.loads(PKG_PATH.read_text(encoding="utf-8"))
        cls.rcpt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))

    def test_positive_schema_and_semantic_validation(self):
        errors = validate_package(self.pkg, self.schema)
        self.assertEqual(errors, [])

    def test_positive_receipt_balance_and_hash(self):
        errors = validate_receipt(self.rcpt)
        self.assertEqual(errors, [])
        expected = hashlib.sha256(canonical_lineage_payload(self.rcpt)).hexdigest()
        self.assertEqual(self.rcpt["lineageHashSha256"], expected)

    def test_reject_missing_human_approval_pin(self):
        m = copy.deepcopy(self.pkg)
        del m["baselineWorkflowChain"][0]["governancePins"]["pin4NamedHumanApproval"]
        self.assertTrue(len(validate_against_json_schema(m, self.schema)) > 0)

    def test_reject_three_canonical_steps(self):
        m = copy.deepcopy(self.pkg)
        m["baselineWorkflowChain"][0]["canonicalSteps"] = m["baselineWorkflowChain"][0]["canonicalSteps"][:3]
        self.assertTrue(len(validate_against_json_schema(m, self.schema)) > 0)

    def test_reject_unknown_constellation(self):
        m = copy.deepcopy(self.pkg)
        m["constellation"] = "UNKNOWN"
        self.assertTrue(len(validate_against_json_schema(m, self.schema)) > 0)

    def test_reject_receipt_exceeding_dollar_cap(self):
        m = copy.deepcopy(self.rcpt)
        m["threeWayMatchDossier"]["vendorInvoice"]["billed_unit_price_usd"] = 14.75
        m["deterministicGateEvaluation"]["unitPriceVarianceUsd"] = 0.25
        m["deterministicGateEvaluation"]["unitPriceVariancePct"] = 0.017241
        m["deterministicGateEvaluation"]["unitPriceVarianceExtendedUsd"] = 300.0
        errs = validate_receipt(m)
        self.assertTrue(
            any("maxAllowedPriceVarianceUsd" in e for e in errs),
            f"Expected dollar-cap rejection, got {errs}",
        )

    def test_all_seven_negative_fixtures_rejected(self):
        for label, mutated in build_negative_fixtures(self.pkg):
            with self.subTest(fixture=label):
                errs = validate_package(mutated, self.schema)
                self.assertTrue(len(errs) > 0, f"Expected failure for {label}")


if __name__ == "__main__":
    unittest.main()
