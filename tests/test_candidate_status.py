from zzaimy.dataset.candidate_status import summarize


def result(status='candidate', at=1, text='evidence', program='p'):
    return dict(context={'program_id':program,'doc_id':1},source_window=[{'id':2,'text':text}],
                status=status,completed_at=at,rows=[{'id':'one'},{'id':'two'}])


def test_latest_hold_does_not_count_old_candidate():
    report = summarize([result(), result('held', at=2)])
    assert report['superseded_same_source_windows'] == 1
    assert report['totals']['held_turns'] == 2
    assert report['totals'].get('candidate_turns', 0) == 0


def test_changed_sources_and_programs_are_not_silently_merged():
    report = summarize([result(),result(text='changed'),result(program='q')])
    assert report['totals']['windows'] == 3
    assert report['totals']['semantic_pending_windows'] == 3
    assert not report['source_freshness_verified']
    assert not report['sft_approval_checked']


def test_semantic_pass_is_not_sft_approval():
    value = result()
    value['semantic_review'] = {'passed':True}
    report = summarize([value])
    assert report['totals'].get('semantic_pending_windows', 0) == 0
    assert not report['sft_approval_checked']
