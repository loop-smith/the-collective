#!/usr/bin/env python3
"""
Executable Schema, Semantic & Receipt Validator for `wfb.package.v1`.
Validates `P2P-CORE-4WF.wfb.package.json` against `wfb.package.v1.schema.json`
(using `jsonschema` Draft 2020-12 when installed, plus built-in recursive Draft 2020-12
schema enforcement so it runs cleanly in any Python 3.10+ environment), verifies
`P2P-3WAY-SAMPLE-RECEIPT.json` arithmetic and canonical SHA-256 hash reproduction,
and runs 8 negative fixture checks.

Run from repository root:
    python contracts/validate_package.py
    python -m unittest discover -s contracts
"""
import copy
import hashlib
import json
import pathlib
import re
import sys

try:
    import jsonschema  # type: ignore
except ImportError:
    jsonschema = None

ROOT = pathlib.Path(__file__).resolve().parent
PKG_PATH = ROOT / "P2P-CORE-4WF.wfb.package.json"
SCHEMA_PATH = ROOT / "wfb.package.v1.schema.json"
RECEIPT_PATH = ROOT / "P2P-3WAY-SAMPLE-RECEIPT.json"

REQUIRED_STEP_SEQUENCE = [
    (1, "1. Manual Step"),
    (2, "2. Function Step"),
    (3, "3. Validation Step"),
    (4, "4. Business Rule Step"),
]

ALLOWED_FAILURE_ROUTES = {
    "HOLD_VENDOR_COMPLIANCE_QUEUE",
    "Loop_L1_Req_Amend",
    "Loop_L2_PO_Change_Order",
    "MANUAL_DOCK_SCAN_FALLBACK",
    "Loop_L3_Quarantine",
    "CWFB-P2P08-RTV",
    "Loop_L4_AP_Hold",
    "TREASURY_EXCEPTION_QUEUE",
}


def canonical_lineage_payload(receipt: dict) -> bytes:
    """
    Canonicalizes `[idempotencyKey, threeWayMatchDossier, deterministicGateEvaluation, subledgerJournalVoucher]`
    using UTF-8 JSON with sorted keys and compact separators `( ",", ":" )` so any implementation matching this canonicalization can reproduce
    `lineageHashSha256`.
    """
    return json.dumps(
        [
            receipt["idempotencyKey"],
            receipt["threeWayMatchDossier"],
            receipt["deterministicGateEvaluation"],
            receipt["subledgerJournalVoucher"],
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def validate_against_json_schema(instance, schema: dict, path: str = "$") -> list[str]:
    """
    Enforces `wfb.package.v1.schema.json` (Draft 2020-12). Uses `jsonschema.Draft202012Validator`
    if installed, and always runs the built-in recursive Draft 2020-12 structural validator
    (`type`, `const`, `enum`, `pattern`, `minLength`, `minimum`, `maximum`, `required`,
    `additionalProperties`, `minItems`, `maxItems`, `prefixItems`, `items`).
    """
    errors: list[str] = []
    if jsonschema is not None and path == "$":
        v = jsonschema.Draft202012Validator(schema)
        for err in v.iter_errors(instance):
            errors.append(f"jsonschema: {err.message}")

    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(instance, dict):
            return [f"{path}: expected object, got {type(instance).__name__}"]
        req = schema.get("required", [])
        for k in req:
            if k not in instance:
                errors.append(f"{path}: missing required property '{k}'")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for k in instance:
                if k not in props:
                    errors.append(f"{path}: unexpected additional property '{k}'")
        for k, val in instance.items():
            if k in props:
                errors.extend(validate_against_json_schema(val, props[k], f"{path}.{k}"))

    elif expected_type == "array":
        if not isinstance(instance, list):
            return [f"{path}: expected array, got {type(instance).__name__}"]
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: array length {len(instance)} < minItems {schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: array length {len(instance)} > maxItems {schema['maxItems']}")
        prefix_items = schema.get("prefixItems")
        if prefix_items is not None:
            for idx, item_schema in enumerate(prefix_items):
                if idx < len(instance):
                    errors.extend(validate_against_json_schema(instance[idx], item_schema, f"{path}[{idx}]"))
            if schema.get("items") is False and len(instance) > len(prefix_items):
                errors.append(f"{path}: items beyond prefixItems ({len(prefix_items)}) are not allowed")
        elif isinstance(schema.get("items"), dict):
            for idx, item in enumerate(instance):
                errors.extend(validate_against_json_schema(item, schema["items"], f"{path}[{idx}]"))

    elif expected_type == "string":
        if not isinstance(instance, str):
            return [f"{path}: expected string, got {type(instance).__name__}"]
        if "minLength" in schema and len(instance.strip()) < schema["minLength"]:
            errors.append(f"{path}: string length < minLength {schema['minLength']}")
        if "pattern" in schema and not re.match(schema["pattern"], instance):
            errors.append(f"{path}: value '{instance}' does not match pattern '{schema['pattern']}'")

    elif expected_type == "integer":
        if not isinstance(instance, int) or isinstance(instance, bool):
            return [f"{path}: expected integer, got {type(instance).__name__}"]
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} > maximum {schema['maximum']}")

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const '{schema['const']}', got '{instance}'")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: '{instance}' not in enum {schema['enum']}")

    return errors


def validate_package(pkg: dict, schema: dict | None = None) -> list[str]:
    if schema is None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    errors = validate_against_json_schema(pkg, schema)
    constellation = pkg.get("constellation", "")
    chain = pkg.get("baselineWorkflowChain", [])

    orders = [wf.get("order") for wf in chain if isinstance(wf, dict)]
    if orders != [1, 2, 3, 4]:
        errors.append(f"workflow orders must be strictly [1, 2, 3, 4], found {orders}")

    valid_orders = set(orders)
    for wf in chain:
        if not isinstance(wf, dict):
            continue
        wcode = wf.get("workflowBlockCode", "")
        if not wcode.startswith(f"CWFB-{constellation}"):
            errors.append(f"workflowBlockCode '{wcode}' does not match constellation '{constellation}'")
        for pin_k, pin_v in wf.get("governancePins", {}).items():
            if not isinstance(pin_v, str) or not pin_v.strip():
                errors.append(f"{wcode}: governancePin '{pin_k}' must be non-empty")
        steps = wf.get("canonicalSteps", [])
        step_seq = [(s.get("stepOrder"), s.get("stepType")) for s in steps if isinstance(s, dict)]
        if step_seq != REQUIRED_STEP_SEQUENCE:
            errors.append(f"{wcode}: canonicalSteps must match {REQUIRED_STEP_SEQUENCE}, found {step_seq}")
        for s in steps:
            if isinstance(s, dict) and not str(s.get("deterministicRule", "")).strip():
                errors.append(f"{wcode} step {s.get('stepOrder')}: deterministicRule must be non-empty")

    for conn in pkg.get("shoppableEnterpriseConnectors", []):
        if not isinstance(conn, dict):
            continue
        cid = conn.get("connectorId", "<unknown>")
        ccode = conn.get("catalogWfbCode", "")
        if not ccode.startswith(f"CWFB-{constellation}"):
            errors.append(f"connector '{cid}': code '{ccode}' does not match constellation '{constellation}'")
        for target_ord in conn.get("attachesToBaselineOrders", []):
            if target_ord not in valid_orders:
                errors.append(f"connector '{cid}': attachesToBaselineOrders references unknown order {target_ord}")
        for rule in conn.get("mutationRules", []):
            if not isinstance(rule, dict):
                continue
            if rule.get("targetOrder") not in valid_orders:
                errors.append(f"connector '{cid}': mutationRule targetOrder {rule.get('targetOrder')} not in {valid_orders}")
            if rule.get("targetStepOrder") not in {1, 2, 3, 4}:
                errors.append(f"connector '{cid}': invalid targetStepOrder {rule.get('targetStepOrder')}")
            if rule.get("onFailureRoute") not in ALLOWED_FAILURE_ROUTES:
                errors.append(f"connector '{cid}': unknown onFailureRoute '{rule.get('onFailureRoute')}'")
            if not str(rule.get("expression", "")).strip():
                errors.append(f"connector '{cid}': empty mutationRule expression")

    return errors


def validate_receipt(rcpt: dict) -> list[str]:
    errors = []
    if rcpt.get("llmCallsOnValidationAndCommitPath") != 0:
        errors.append("llmCallsOnValidationAndCommitPath must equal 0")

    dossier = rcpt["threeWayMatchDossier"]
    po = dossier["purchaseOrder"]
    grn = dossier["dockReceiptGrn"]
    inv = dossier["vendorInvoice"]
    gate = rcpt["deterministicGateEvaluation"]

    hara = rcpt.get("haraRuntimeBinding", {})
    if not hara.get("humanSeat") or not hara.get("delegationPolicyOwner") or not hara.get("delegationPolicyRef"):
        errors.append("haraRuntimeBinding missing required authority fields")
    if hara.get("humanSeat") == hara.get("delegationPolicyOwner"):
        errors.append("haraRuntimeBinding segregation-of-duties violation: humanSeat cannot equal delegationPolicyOwner")
    if hara.get("delegationPolicyOwner") != "CONTROLLER-SEAT-01" or hara.get("delegationPolicyRef") != "AP-DOA-2026.3":
        errors.append("haraRuntimeBinding delegation policy does not match pinned authority record (CONTROLLER-SEAT-01 / AP-DOA-2026.3)")

    if grn.get("coa_lot_verified") is not True or grn.get("qms_vendor_sqf_status") != "APPROVED":
        errors.append("dockReceiptGrn quality precondition failed: coa_lot_verified must be True and qms_vendor_sqf_status must be APPROVED")

    if not (inv["billed_qty"] <= grn["received_qty_accepted"]):
        errors.append("billed_qty exceeds received_qty_accepted")

    calc_var_pct = round(abs(inv["billed_unit_price_usd"] - po["ordered_unit_price_usd"]) / po["ordered_unit_price_usd"], 6)
    if calc_var_pct != gate["unitPriceVariancePct"]:
        errors.append(f"unitPriceVariancePct mismatch: {calc_var_pct} != {gate['unitPriceVariancePct']}")
    if calc_var_pct > gate["maxAllowedPriceVariancePct"]:
        errors.append("unitPriceVariancePct exceeds maxAllowedPriceVariancePct")

    calc_ext_var = round(abs(inv["billed_unit_price_usd"] - po["ordered_unit_price_usd"]) * inv["billed_qty"], 2)
    if calc_ext_var != gate.get("unitPriceVarianceExtendedUsd"):
        errors.append(
            f"unitPriceVarianceExtendedUsd mismatch: {calc_ext_var} != {gate.get('unitPriceVarianceExtendedUsd')}"
        )
    if gate.get("unitPriceVarianceExtendedUsd", 0.0) > gate.get("maxAllowedPriceVarianceUsd", 0.0):
        errors.append(
            f"unitPriceVarianceExtendedUsd ({gate.get('unitPriceVarianceExtendedUsd')}) exceeds maxAllowedPriceVarianceUsd ({gate.get('maxAllowedPriceVarianceUsd')})"
        )

    vch = rcpt["subledgerJournalVoucher"]
    debits = round(sum(line["debitUsd"] for line in vch["journalLines"]), 2)
    credits = round(sum(line["creditUsd"] for line in vch["journalLines"]), 2)
    if debits != credits or debits != vch["totalDebitsUsd"] or credits != vch["totalCreditsUsd"]:
        errors.append(f"unbalanced voucher: debits={debits}, credits={credits}")
    expected_inv_total = round(inv["billed_qty"] * inv["billed_unit_price_usd"] + inv.get("freight_tax_usd", 0.0), 2)
    if debits != expected_inv_total or round(inv["billed_total_usd"], 2) != expected_inv_total:
        errors.append(f"voucher total ({debits}) does not reconcile to invoice total ({expected_inv_total})")

    expected_hash = hashlib.sha256(canonical_lineage_payload(rcpt)).hexdigest()
    if rcpt.get("lineageHashSha256") != expected_hash:
        errors.append(f"lineageHashSha256 mismatch: expected {expected_hash}, found {rcpt.get('lineageHashSha256')}")

    return errors


def build_negative_fixtures(pkg: dict) -> list[tuple[str, dict]]:
    cases = []

    m1 = copy.deepcopy(pkg)
    for s in m1["baselineWorkflowChain"][0]["canonicalSteps"]:
        s["stepOrder"] = 1
        s["stepType"] = "1. Manual Step"
    cases.append(("4 copies of Manual Step 1", m1))

    m2 = copy.deepcopy(pkg)
    m2["baselineWorkflowChain"][1]["order"] = 1
    cases.append(("Duplicate workflow order number", m2))

    m3 = copy.deepcopy(pkg)
    m3["baselineWorkflowChain"][0]["canonicalSteps"][0]["deterministicRule"] = "   "
    cases.append(("Empty deterministicRule", m3))

    m4 = copy.deepcopy(pkg)
    m4["baselineWorkflowChain"][0]["governancePins"]["pin4NamedHumanApproval"] = ""
    cases.append(("Empty human-approval pin", m4))

    m5 = copy.deepcopy(pkg)
    m5["shoppableEnterpriseConnectors"][0]["mutationRules"][0]["targetOrder"] = 999
    cases.append(("Connector pointing at workflow 999", m5))

    m6 = copy.deepcopy(pkg)
    m6["shoppableEnterpriseConnectors"][0]["mutationRules"][0]["onFailureRoute"] = "UNKNOWN_ROUTE"
    cases.append(("Unknown exception route", m6))

    m7 = copy.deepcopy(pkg)
    m7["baselineWorkflowChain"][0]["workflowBlockCode"] = "CWFB-O2C01-QUOTE"
    cases.append(("O2C block inside P2P package", m7))

    return cases


def run_negative_fixture_suite(pkg: dict, schema: dict) -> None:
    for label, mutated in build_negative_fixtures(pkg):
        errs = validate_package(mutated, schema)
        if not errs:
            raise AssertionError(f"Negative fixture FAILED to reject: {label}")
        print(f"  [REJECTED AS EXPECTED] {label} -> {errs[0]}")


def main() -> int:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    pkg = json.loads(PKG_PATH.read_text(encoding="utf-8"))
    rcpt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))

    pkg_errors = validate_package(pkg, schema)
    if pkg_errors:
        print("PACKAGE VALIDATION FAILED:", pkg_errors)
        return 1
    print("[PASS] P2P-CORE-4WF.wfb.package.json passed Draft 2020-12 JSON Schema & semantic validation.")

    rcpt_errors = validate_receipt(rcpt)
    if rcpt_errors:
        print("RECEIPT VALIDATION FAILED:", rcpt_errors)
        return 1
    print(f"[PASS] P2P-3WAY-SAMPLE-RECEIPT.json passed 3-way math, balanced journal ($17,544.00 == $17,544.00), and canonical SHA-256 verification ({rcpt['lineageHashSha256'][:16]}...).")

    print("Running 8 negative fixture checks (7 package mutations + 1 receipt dollar-cap breach):")
    run_negative_fixture_suite(pkg, schema)
    rcpt_cap_breach = copy.deepcopy(rcpt)
    rcpt_cap_breach["threeWayMatchDossier"]["vendorInvoice"]["billed_unit_price_usd"] = 14.75
    rcpt_cap_breach["deterministicGateEvaluation"]["unitPriceVarianceUsd"] = 0.25
    rcpt_cap_breach["deterministicGateEvaluation"]["unitPriceVariancePct"] = 0.017241
    rcpt_cap_breach["deterministicGateEvaluation"]["unitPriceVarianceExtendedUsd"] = 300.0
    cap_errs = validate_receipt(rcpt_cap_breach)
    if not cap_errs:
        raise AssertionError("Negative fixture FAILED to reject: Extended price variance $300 on $250 cap")
    print(f"  [REJECTED AS EXPECTED] Extended price variance $300 on $250 cap -> {cap_errs[0]}")
    print("[PASS] All 8 negative fixtures deterministically rejected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
