"""Compare saved runs without hiding invalid runs or implying statistical significance."""
import argparse
import json
from pathlib import Path
from statistics import median


def summarize(directory: Path) -> dict:
    runs=[]
    for path in sorted(directory.glob('*.json')):
        record=json.loads(path.read_text())
        if 'case' in record and 'status' in record:
            runs.append(record)
    if not runs:
        raise ValueError(f'No evaluation runs in {directory}')
    cases={}
    for case_id in sorted({r['case'] for r in runs}):
        group=[r for r in runs if r['case']==case_id]
        cases[case_id]={
            'runs':len(group), 'invalid':sum(r['status']!='completed' for r in group),
            'rubric_passes':sum(r.get('score',{}).get('passed') is True for r in group),
            'rubric_failures':sum(r.get('score',{}).get('passed') is False for r in group),
            'ungraded':sum(r['status']=='completed' and r.get('score',{}).get('passed') is None for r in group),
            'median_seconds':median(r['elapsed_seconds'] for r in group),
            'verdicts':[r.get('assessment',{}).get('verdict') for r in group],
            'case_sha256':sorted({r['case_sha256'] for r in group})}
    return {'directory':str(directory),'cases':cases,
            'models':sorted({r['model'] for r in runs}),
            'budgets':sorted({r['max_iters'] for r in runs})}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline',type=Path)
    parser.add_argument('candidate',type=Path)
    args=parser.parse_args()
    before,after=summarize(args.baseline),summarize(args.candidate)
    warnings=[]
    if before['models']!=after['models'] or before['budgets']!=after['budgets']:
        warnings.append('Model or iteration budgets differ; not a controlled comparison.')
    if before['cases'].keys()!=after['cases'].keys():
        warnings.append('Case sets differ.')
    for key in before['cases'].keys() & after['cases'].keys():
        if before['cases'][key]['case_sha256'] != after['cases'][key]['case_sha256']:
            warnings.append(f'{key}: case contents differ.')
        if before['cases'][key]['runs'] != after['cases'][key]['runs']:
            warnings.append(f'{key}: repetition counts differ.')
    print(json.dumps({'baseline':before,'candidate':after,'warnings':warnings,
                      'interpretation':'Descriptive counts only; inspect transcripts and held-out binaries before claiming improvement.'},
                     ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
