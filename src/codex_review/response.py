"""Section 8 — validating the Codex response. Violations are exit 8."""

import json

VERDICTS = ("approve", "changes-required")
CLASSIFICATIONS = ("introduced", "pre-existing")
SEVERITIES = ("blocker", "major", "minor")
CLAIM_RESULTS = ("holds", "refuted", "not-verifiable")

FINDING_FIELDS = (
    "id", "classification", "severity", "claim",
    "failure_scenario", "evidence", "artifact", "suggested_fix",
)


class ContractViolation(Exception):
    def __init__(self, code, message, **details):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.details = details


def _require(condition, code, message, **details):
    if not condition:
        raise ContractViolation(code, message, **details)


def validate(raw_text, target):
    """Parse and check the response against §8. Returns the parsed object."""
    try:
        parsed = json.loads(raw_text)
    except ValueError as error:
        raise ContractViolation("malformed-json", "response is not valid JSON",
                                detail=str(error))
    _require(isinstance(parsed, dict), "not-an-object", "response is not a JSON object")

    verdict = parsed.get("verdict")
    _require(verdict in VERDICTS, "bad-verdict", "unrecognised verdict", verdict=verdict)

    findings = parsed.get("findings")
    _require(isinstance(findings, list), "bad-findings", "findings must be a list")

    artifacts = set(target.get("artifacts") or ())
    ids = set()
    introduced = []
    for finding in findings:
        _require(isinstance(finding, dict), "bad-finding", "a finding is not an object")
        missing = [f for f in FINDING_FIELDS if f not in finding]
        _require(not missing, "finding-missing-fields", "a finding is missing fields",
                 id=finding.get("id"), missing=missing)
        _require(finding["id"] not in ids, "duplicate-finding-id",
                 "finding ids must be unique", id=finding["id"])
        ids.add(finding["id"])
        _require(finding["classification"] in CLASSIFICATIONS, "bad-classification",
                 "unrecognised classification", id=finding["id"])
        _require(finding["severity"] in SEVERITIES, "bad-severity",
                 "unrecognised severity", id=finding["id"])
        artifact = finding["artifact"]
        _require(artifact is None or artifact in artifacts, "unknown-artifact",
                 "finding names an artifact the target does not have",
                 id=finding["id"], artifact=artifact)
        if finding["classification"] == "introduced":
            # Only a pre-existing finding may map to no artifact (§3).
            _require(artifact is not None, "introduced-without-artifact",
                     "an introduced finding must name the artifact it occurs in",
                     id=finding["id"])
            introduced.append(finding["id"])

    # Verdict coherence, both directions.
    _require(bool(introduced) == (verdict == "changes-required"), "verdict-incoherent",
             "verdict does not agree with the reported findings",
             verdict=verdict, introduced=introduced)

    declared = parsed.get("declared_scope")
    _require(isinstance(declared, list), "bad-declared-scope", "declared_scope must be a list")
    uncovered = sorted(artifacts - set(declared))
    _require(not uncovered, "declared-scope-incomplete",
             "declared_scope does not cover the job's artifacts", uncovered=uncovered)

    if target.get("mode") == "plan":
        claims = parsed.get("verified_claims")
        _require(isinstance(claims, list), "bad-verified-claims",
                 "verified_claims must be a list for a plan target")
        context_free = target.get("context_repository") is None
        for claim in claims:
            _require(isinstance(claim, dict), "bad-claim", "a claim is not an object")
            result = claim.get("result")
            _require(result in CLAIM_RESULTS, "bad-claim-result",
                     "unrecognised claim result", result=result)
            if context_free:
                # No repository was captured, so no claim can have been settled against one.
                _require(result == "not-verifiable", "claim-settled-context-free",
                         "context-free review reported a settled claim", result=result)
            if result == "refuted":
                _require(claim.get("finding_id") in introduced, "refuted-without-finding",
                         "a refuted claim must name an introduced finding",
                         finding_id=claim.get("finding_id"))
    return parsed
