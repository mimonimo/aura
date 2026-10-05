import pytest
from zzaimy.dataset.candidate_review import review_receipt, review_matches, save_review
from zzaimy.dataset.candidate_status import summarize


def test_review_is_bound_to_candidate_and_never_human_approval():
    candidate = {'job':'abc','rows':[{'id':'one','answer':'first'}]}
    receipt = review_receipt(candidate, {'decision':'rework_required','issues':[
        {'sample_id':'one','reason':'unrelated metric evidence','action':'rewrite with matching evidence'}]},
        reviewer='codex', reviewed_at='now')
    assert review_matches(candidate, receipt)
    assert receipt['reviewer_type'] == 'agent' and not receipt['approved']
    candidate['rows'][0]['answer'] = 'changed'
    assert not review_matches(candidate, receipt)


def test_review_cannot_target_unseen_sample():
    with pytest.raises(ValueError):
        review_receipt({'job':'abc','rows':[]}, {'decision':'rework_required','issues':[
            {'sample_id':'one','reason':'bad','action':'rewrite'}]}, reviewer='codex',reviewed_at='now')


def test_saved_review_excludes_rework_but_not_changed_candidates(tmp_path):
    candidate = {'job':'abc','rows':[{'id':'one','answer':'bad'}],
                 'context':{'program_id':'p','doc_id':1}, 'source_window':[],
                 'status':'candidate','completed_at':1}
    findings = {'decision':'rework_required','issues':[
        {'sample_id':'one','reason':'wrong evidence','action':'rewrite'}]}
    path = save_review(tmp_path, candidate, findings, reviewer='codex', reviewed_at='now')
    assert path == save_review(tmp_path, candidate, findings, reviewer='codex', reviewed_at='now')
    import json
    receipt = json.loads(path.read_text())
    totals = summarize([candidate], [receipt])['totals']
    assert totals['rework_required_turns'] == 1
    assert totals.get('candidate_turns', 0) == 0
    candidate['rows'][0]['answer'] = 'corrected'
    assert summarize([candidate], [receipt])['totals']['candidate_turns'] == 1
