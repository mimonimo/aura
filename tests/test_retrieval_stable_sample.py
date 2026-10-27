from zzaimy.eval.retrieval_eval import Query, stable_sample


def _qs(n):
    return [Query(text=f"질의 {i}", qtype="k", row=i) for i in range(n)]


def test_one_added_query_changes_at_most_one_member():
    a = _qs(1000)
    b = a[:500] + [Query(text="새 질의", qtype="k", row=10_000)] + a[500:]
    sa = {(a[j].row, a[j].text) for j in stable_sample(a, 300, 7)}
    sb = {(b[j].row, b[j].text) for j in stable_sample(b, 300, 7)}
    assert len(sa) == len(sb) == 300
    assert len(sa - sb) <= 1


def test_sorted_and_bounded():
    q = _qs(5)
    idx = stable_sample(q, 300, 7)
    assert idx == sorted(idx) == list(range(5))
