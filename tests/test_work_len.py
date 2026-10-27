from zzaimy.app import llm_budget


class _C:
    def __init__(self, ctx, work=0):
        self.context_default = ctx
        self.model = f"m{ctx}"
        self.work_context = work


def test_work_len_follows_context_unless_capped(monkeypatch):
    monkeypatch.delenv("ZZAIMY_WORK_CONTEXT", raising=False)
    monkeypatch.setattr(llm_budget, "context_len", lambda c: c.context_default)
    assert llm_budget.work_len(_C(262144)) == 262144                      # 기본은 묶지 않는다
    assert llm_budget.work_len(_C(262144, work=32768)) == 32768          # 연결 설정으로만 묶는다
    monkeypatch.setenv("ZZAIMY_WORK_CONTEXT", "100000")
    assert llm_budget.work_len(_C(262144)) == 100000
    assert llm_budget.work_len(_C(16384)) == 16384
