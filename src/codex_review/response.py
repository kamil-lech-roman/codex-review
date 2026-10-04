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


def decimal_integer(value):
    if not isinstance(value, str) or not value.isdecimal():
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse(raw_text, label="response"):
    if isinstance(raw_text, bytes):
        try:
            raw_text = raw_text.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ContractViolation("malformed-encoding", label + " is not UTF-8",
                                    detail=str(error))
    try:
        return json.loads(raw_text)
    except ValueError as error:
        raise ContractViolation("malformed-json", label + " is not valid JSON",
                                detail=str(error))


def validate(raw_text, target):
    """Parse and check the response against §8. Returns the parsed object."""
    parsed = parse(raw_text)
    _require(isinstance(parsed, dict), "not-an-object", "response is not a JSON object")

    verdict = parsed.get("verdict")
    _require(verdict in VERDICTS, "bad-verdict", "unrecognised verdict", verdict=verdict)

    findings = parsed.get("findings")
    _require(isinstance(findings, list), "bad-findings", "findings must be a list")

    artifacts = set(target.get("artifacts") or ())
    ids = set()
    introduced = []
    for index, finding in enumerate(findings):
        _require(isinstance(finding, dict), "bad-finding", "a finding is not an object")
        missing = [f for f in FINDING_FIELDS if f not in finding]
        _require(not missing, "finding-missing-fields", "a finding is missing fields",
                 id=finding.get("id"), missing=missing)
        for field in FINDING_FIELDS:
            _text(finding[field], "findings[{}].{}".format(index, field), nullable=field == "artifact")
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
    for index, value in enumerate(declared):
        _text(value, "declared_scope[{}]".format(index))
    _require(len(set(declared)) == len(declared) and not set(declared) - artifacts,
             "bad-declared-scope", "scope must name each captured artifact exactly once",
             field="declared_scope", expected=sorted(artifacts))
    uncovered = sorted(artifacts - set(declared))
    _require(not uncovered, "declared-scope-incomplete",
             "declared_scope does not cover the job's artifacts", uncovered=uncovered)

    if target.get("mode") == "plan":
        claims = parsed.get("verified_claims")
        _require(isinstance(claims, list), "bad-verified-claims",
                 "verified_claims must be a list for a plan target")
        context_free = target.get("context_repository") is None
        for index, claim in enumerate(claims):
            _require(isinstance(claim, dict), "bad-claim", "a claim is not an object")
            for field in ("claim", "result", "evidence", "finding_id"):
                _require(field in claim, "claim-missing-field", "claim is missing a field",
                         field="verified_claims[{}].{}".format(index, field), expected="required")
                _text(claim[field], "verified_claims[{}].{}".format(index, field),
                      nullable=field == "finding_id")
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
    from codex_review.reviewer import response_schema
    _closed_objects(parsed, response_schema(target.get('mode')), 'response')
    return parsed


def _closed_objects(value, schema, field):
    if schema.get('type') == 'object' and isinstance(value, dict):
        properties = schema['properties']
        unknown = sorted(set(value) - set(properties))
        _require(not unknown, 'unknown-response-fields', 'response contains unknown fields',
                 field=field, unknown=unknown, expected=sorted(properties))
        for key, child in value.items():
            _closed_objects(child, properties[key], field + '.' + key)
    elif schema.get('type') == 'array' and isinstance(value, list):
        for index, child in enumerate(value):
            _closed_objects(child, schema['items'], '{}[{}]'.format(field, index))


def _text(value, field, nullable=False):
    _require((nullable and value is None) or (isinstance(value, str) and bool(value.strip())),
             "bad-field-type", "field must contain text", field=field,
             expected="nonempty string or null" if nullable else "nonempty string")
