"""One quiescence rule for pending-call recovery and late retired output."""
import os
import socket

from codex_review import loops, probes, response, state, transport


def require(launch, decision, evidence_job, *, completion=False):
    process = transport.process_state(launch)
    citations = decision.get('quiescence_evidence_ids')
    cited = (process == 'unknown' and decision.get('asserted_by') == 'user' and
             decision.get('quiescence_host') == socket.gethostname() and
             (not launch or launch.get('host') == socket.gethostname()) and
             isinstance(citations, list) and bool(citations))
    if cited:
        for citation in citations:
            response._require(isinstance(citation, str) and state.is_job_id(citation),
                              'bad-evidence-id', 'cite sealed quiescence evidence')
            metadata = loops._json(os.path.join(evidence_job, 'evidence', citation, 'metadata.json'))
            probes._rehash(evidence_job, os.path.basename(evidence_job), citation, metadata['source_path'])
    response._require(process != 'live' and (completion or process in ('absent', 'reused', 'exited') or cited),
                      'call-not-quiescent', 'finish the matching child or establish quiescence before recovery',
                      process_state=process)
    return dict(process_state=process, evidence_ids=citations if cited else [], asserted_by='user' if cited else 'driver')
