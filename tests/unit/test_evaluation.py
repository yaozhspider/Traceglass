from traceglass.evaluation.scoring import score


def test_rubric_rejects_confident_benign_and_fabricated_evidence_in_blocked_case():
    case={'evidence':[{'id':'E1'}], 'rubric':{'allowed_verdicts':['unknown'],
          'required_evidence':['E1'], 'requires_gaps':True,'requires_next_steps':True}}
    output=dict(verdict='benign_in_scope',evidence_refs=['made-up'],dataflow='unexamined',
                authorization='unknown',privilege='unknown',gaps=[],next_steps=[],explanation='safe')
    bad=score(case,output)
    assert not bad['passed']
    assert set(bad['failures']) == {'wrong_verdict','missing_or_invented_evidence_refs',
        'key_evidence_not_cited','missing_coverage_gaps','missing_next_steps'}
    output.update(verdict='unknown',evidence_refs=['E1'],gaps=['receiver unexamined'],
                  next_steps=['decompile receiver'])
    assert score(case,output)['passed']


def test_historical_badcase_is_rejected_and_citations_cannot_be_coverage_ids():
    import json
    from pathlib import Path
    import pytest
    from pydantic import ValidationError
    from traceglass.evaluation.scoring import replay_schema
    case=json.loads((Path(__file__).parents[2]/'evals/cases/pyinstaller-incomplete.json').read_text())
    assert not score(case,case['historical_assessment'])['passed']
    data={**case['historical_assessment'],'evidence_refs':['path-created-by-model']}
    with pytest.raises(ValidationError):
        replay_schema(case).model_validate(data)


def test_comparison_keeps_invalid_and_ungraded_binary_runs_visible(tmp_path):
    import json
    from traceglass.evaluation.compare import summarize
    base=dict(case='case',model='fixture',max_iters=4,case_sha256='x',elapsed_seconds=1)
    for index,extra in enumerate([dict(status='invalid'),
                                  dict(status='completed',score={'passed':None}),
                                  dict(status='completed',score={'passed':False})]):
        (tmp_path/f'{index}.json').write_text(json.dumps({**base,**extra}))
    result=summarize(tmp_path)['cases']['case']
    assert result['runs']==3 and result['invalid']==1 and result['ungraded']==1
    assert result['rubric_failures']==1 and result['rubric_passes']==0
