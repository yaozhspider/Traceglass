"""Compare saved runs with visible failures; small suites are descriptive only."""
import argparse
import json
from pathlib import Path
from statistics import median

from .telemetry import token_metrics

COMMON = ('model', 'model_parameters', 'max_iters', 'timeout', 'evaluation_type',
          'analysis_goal', 'sample_sha256', 'cache_condition', 'rea_image_id', 'rea_schema_sha256',
          'concurrency', 'model_retry_budget', 'agent_model_retry_budget')


def _median(group, getter):
    values = [value for r in group if (value := getter(r)) is not None]
    return median(values) if values else None


def summarize(directory: Path) -> dict:
    runs = []
    for path in sorted(directory.glob('*.json')):
        record = json.loads(path.read_text())
        if 'case' in record and 'status' in record:
            runs.append(record)
    if not runs:
        raise ValueError(f'No evaluation runs in {directory}')
    cases = {}
    for case_id in sorted({r['case'] for r in runs}):
        group = [r for r in runs if r['case'] == case_id]
        cases[case_id] = dict(runs=len(group),
            invalid=sum(r['status'] == 'invalid' for r in group),
            timeout=sum(r['status'] == 'timeout' for r in group),
            rubric_passes=sum(r.get('score', {}).get('passed') is True for r in group),
            rubric_failures=sum(r.get('score', {}).get('passed') is False for r in group),
            ungraded=sum(r['status'] in {'completed', 'ungraded'} and r.get('score', {}).get('passed') is None for r in group),
            median_seconds=_median(group, lambda r: r.get('elapsed_seconds')),
            median_total_tokens=_median(group, lambda r: token_metrics(r.get('model_usage', []))['total_tokens']),
            median_tool_calls=_median(group, lambda r: r.get('tool_metrics', {}).get('calls')),
            median_tool_failures=_median(group, lambda r: r.get('tool_metrics', {}).get('failures')),
            median_container_starts=_median(group, lambda r: r.get('container_metrics', {}).get('starts')),
            median_connection_seconds=_median(group, lambda r: r.get('container_metrics', {}).get('connection_seconds')),
            median_key_fact_recall=_median(group, lambda r: r.get('score', {}).get('key_fact_recall')),
            verdicts=[r.get('assessment', {}).get('verdict') for r in group],
            case_sha256=sorted({r.get('case_sha256', '') for r in group}),
            fingerprints={key: sorted({json.dumps(r.get(key), sort_keys=True) for r in group})
                          for key in ('prompt_sha256', 'skill_hashes', 'code_sha256', 'working_diff_sha256',
                                      'git_revision', 'assessment_schema_sha256', 'assembled_prompt_hashes',
                                      'exposed_tool_schema_hashes')},
            common_parameters={key: sorted({json.dumps(r.get(key), sort_keys=True) for r in group}) for key in COMMON})
    return dict(directory=str(directory), cases=cases,
                models=sorted({r.get('model', '') for r in runs}), budgets=sorted({r.get('max_iters', 0) for r in runs}))


def compare(baseline, candidate):
    before, after = summarize(baseline), summarize(candidate)
    warnings = []
    if before['cases'].keys() != after['cases'].keys():
        warnings.append('Case sets differ.')
    for key in before['cases'].keys() & after['cases'].keys():
        old, new = before['cases'][key], after['cases'][key]
        if old['case_sha256'] != new['case_sha256']:
            warnings.append(f'{key}: case contents differ.')
        if old['runs'] != new['runs']:
            warnings.append(f'{key}: repetition counts differ.')
        for parameter in COMMON:
            if old['common_parameters'][parameter] != new['common_parameters'][parameter]:
                warnings.append(f'{key}: {parameter} differs; not a controlled comparison.')
    return dict(baseline=before, candidate=after, warnings=warnings,
                interpretation='Descriptive counts only; labels and patterns are not semantic proof. '
                'Inspect original evidence and held-out binaries before claiming improvement.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    print(json.dumps(compare(args.baseline, args.candidate), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
