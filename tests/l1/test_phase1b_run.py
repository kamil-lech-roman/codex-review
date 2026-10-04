"""One-pass invocation provenance without executable identity gating (D10)."""
from pathlib import Path

from test_run import VALID_RESPONSE


def should_launch_in_the_home_resolved_before_changing_to_the_job_directory(
        review, plan_file_factory, fake_codex, codex_reply, tmp_path):
    import json
    # given a relative execution home resolved from the invoking host's cwd
    home = tmp_path / 'review-home'
    home.mkdir()
    observed = tmp_path / 'observed-home.json'
    fake_codex.write_text(fake_codex.read_text().replace('argv = sys.argv[1:]',
        "argv = sys.argv[1:]\njson.dump(os.path.realpath(os.environ['CODEX_HOME']), open(" + repr(str(observed)) + ", 'w'))"))
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo', '--no-verify',
                      '--reason', 'home binding control', cwd=tmp_path, fake={'CODEX_HOME': 'review-home'})
    assert prepared.exit_code == 0, prepared
    codex_reply(VALID_RESPONSE)
    # when run changes cwd for the child, then the configured home still denotes the same absolute directory
    result = review('run', prepared.envelope['job_id'], cwd=tmp_path,
                    fake={'CODEX_HOME': 'review-home', 'CODEX_FAKE_RESPONSE': codex_reply.path})
    assert result.exit_code == 0, result
    assert json.loads(observed.read_text()) == str(home)


def should_launch_resolved_executable_without_querying_its_version(
        review, plan_file_factory, fake_codex, codex_reply, tmp_path):
    # given an executable whose version query leaves evidence
    marker = tmp_path / 'version-was-queried'
    fake_codex.write_text(fake_codex.read_text().replace(
        "    print('codex-cli 0.0.0-fake'); sys.exit(0)",
        f"    open({str(marker)!r}, 'w').write('queried'); sys.exit(0)"))
    prepared = review('prepare', 'plan', str(plan_file_factory()), '--no-repo',
                      '--no-verify', '--reason', 'inspection')
    codex_reply(VALID_RESPONSE)
    # when reviewing a valid frozen capture
    result = review('run', prepared.envelope['job_id'],
                    fake={'CODEX_FAKE_RESPONSE': codex_reply.path})
    # then retain launch-path provenance but do not query or record CLI version
    assert result.exit_code == 0, result
    assert not marker.exists()
    metadata = result.envelope['payload']['run_metadata']
    assert metadata['codex_path'] == str(fake_codex)
    assert 'codex_version' not in metadata
