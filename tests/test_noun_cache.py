"""규정 명사 캐시 파일 — 서비스를 다시 띄워도 형태소 분석을 다시 하지 않는다(첫 질문 지연 1분 → 파일 읽기)."""
import json

from zzaimy.app import regulations as R


def test_noun_cache_persists_and_reloads(tmp_path, monkeypatch):
    idx = tmp_path / "index"
    idx.mkdir()
    monkeypatch.setenv("ZZAIMY_EMBED_INDEX", str(idx / "chunk_embeddings.npz"))
    monkeypatch.setattr(R, "_noun_cache", {})
    monkeypatch.setattr(R, "_noun_file_state", {"loaded": False, "dirty": 0})
    calls = []
    monkeypatch.setattr(R, "extract_nouns", lambda t: calls.append(t) or frozenset({"휴학", "신청"}))
    R.chunk_nouns({"content": "휴학은 학기 단위로 신청한다."})
    R.save_noun_cache()
    saved = json.loads((idx / "reg_nouns.json").read_text(encoding="utf-8"))
    assert list(saved.values()) == [["신청", "휴학"]] and len(calls) == 1
    # 새 프로세스처럼 — 메모리를 비우고 다시: 파일에서 읽어 분석하지 않는다
    monkeypatch.setattr(R, "_noun_cache", {})
    monkeypatch.setattr(R, "_noun_file_state", {"loaded": False, "dirty": 0})
    assert R.chunk_nouns({"content": "휴학은 학기 단위로 신청한다."}) == frozenset({"휴학", "신청"})
    assert len(calls) == 1


def test_no_file_without_index_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZAIMY_EMBED_INDEX", str(tmp_path / "없는폴더" / "chunk_embeddings.npz"))
    monkeypatch.setattr(R, "_noun_file_state", {"loaded": False, "dirty": 5})
    R.save_noun_cache()
    assert not (tmp_path / "없는폴더").exists()
