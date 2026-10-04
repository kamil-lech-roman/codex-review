# Authentication diagnostic evidence

The [portable probe](auth-status-probe.py) and [historical observations](observations.json)
record a bounded Codex-hosted diagnostic from 2026-09-22, using Claude 2.1.268.
The probe source is unchanged; the result is a labeled projection of the private
original, with its executable locator replaced. Source digests identify that original;
they do not make the projection a complete raw transcript.

With no synthetic key, auth status reported `claude.ai` and subscription type `pro`.
With a synthetic invalid key, status still reported `claude.ai`, but subscription
type became null and the API-key source was the environment. One tiny request
then failed with `Invalid API key`. No HTTP status, successful subscription request,
real API charge, fallback path, Opus behavior or full adapter isolation was established.
A separate private, binary-pinned selector probe used synthetic credentials and
stubs; it established only that isolated selector's behavior, not request routing.

Inspect status without a model call:

```sh
python3 docs/evidence/claude-auth/auth-status-probe.py --claude /path/to/claude
```

Adding `--request-with-invalid-key` makes a live diagnostic request and may consume
subscription quota if another credential wins. It is not production route qualification.
The [adapter acceptance gate](../../plans/claude-opus-adapter.md#delivery-and-acceptance)
owns the required replacement probes and supported-route checks.
