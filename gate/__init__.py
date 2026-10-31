"""Rule gate entry point; behaviour remains a separately owned module."""


def run_gate(rule_id, candidate_dir, log=None, sources=None, **kwargs):
    from gate.run import run_gate as run
    return run(rule_id, candidate_dir, log=log, sources=sources, **kwargs)
