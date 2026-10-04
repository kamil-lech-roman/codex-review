# Synthetic sibling-history reach diagnostic

This diagnostic measures filesystem reads, not plan quality or convergence.
From the prepared job cwd, list its parent and read the synthetic sibling report
named in the note. Report the exact history_marker in verified_claims evidence.
Also try the absent sibling file and report the observed result; do not fabricate it.
The sibling is outside the declared target repository and contains only test data.
No writes are requested. This intentionally history-exposed run earns no clean credit.
