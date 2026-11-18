"""「가)⇥본문」 처럼 hp:t 안에 탭이 낀 문단도 글로 읽는다(11-18 — 다시 읽기가 8절 항목을 빈 문단으로 읽었다)."""
from zzaimy.app import doc_review as r


def test_txt_reads_text_around_tab():
    xml = '<hp:p><hp:run><hp:t>가)<hp:tab width="2255" leader="0" type="1"/>AI 기초 교과 이수율 95%</hp:t></hp:run></hp:p>'
    assert r._txt(xml) == "가) AI 기초 교과 이수율 95%"
