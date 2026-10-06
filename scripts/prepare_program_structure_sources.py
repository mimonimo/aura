"""반입 대장의 사업-문서유형 관계만 추출한다. 파일 전문·파일명·수치는 학습 입력에 넣지 않는다."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from zzaimy.dataset.tracks import key


def main():
    from dotenv import load_dotenv
    load_dotenv(".env.local")
    from zzaimy.app.db import Database
    from zzaimy.app.archive_view import KIND_KO
    db=Database(Path("data/platform/platform.db"))
    with db._conn() as conn:
        rows=conn.execute("SELECT program,kind FROM archive_files WHERE removed_at='' AND program<>'' AND kind<>'' GROUP BY program,kind").fetchall()
    groups={}
    allowed={"announcement","basic_plan","criteria","evaluation","form","guideline","plan","report","regulation"}
    for program,kind in rows:
        if kind in allowed:groups.setdefault(program,set()).add(kind)
    root=Path("data/training/tracks/program_retrieval");root.mkdir(parents=True,exist_ok=True)
    manifest={"track":"program_retrieval","source_mode":"existing_inventory_structure_only","sources":[]}
    seen=set()
    for program,kinds in sorted(groups.items()):
        signature=tuple(sorted(kinds))
        if len(kinds)<2 or signature in seen:continue
        seen.add(signature)
        profile={"structure":"사업 분류 후보 아래 연결된 문서 유형", "document_types":[KIND_KO[k] for k in signature],
                 "constraints":["원본 대장의 분류는 후보이며 실제 원문으로 사업과 연도를 확인해야 함",
                    "목록에 있다는 사실은 내용 검증·권한 허용·OCR 완료를 뜻하지 않음",
                    "담당자가 요청한 사업·연도·작성 목적을 확인한 뒤 공고·지침·서식 선택",
                    "같은 사업에 속해도 연도·연차·계획과 실적을 구분하여 조회",
                    "사용자 역할·문서 권한은 서버가 필터링하며 제한된 본문·제목·관계를 노출하지 않음",
                    "사용할 자료가 없으면 다른 사업으로 대체하지 않고 필요한 문서 요청",
                    "목표는 검색 대상과 근거 활용 방법이며 개별 사업의 사실 암기가 아님"]}
        text=json.dumps(profile,ensure_ascii=False,indent=2)
        sid=key([program,signature])
        path=root/(sid+".txt");path.write_text(text)
        manifest["sources"].append({"id":sid,"title":"반입 대장 구조 유형","text_path":str(path),"text_sha256":key(text)})
        if len(manifest["sources"])>=12:break
    (root/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps({"structure_profiles":len(manifest["sources"]),"source_mode":manifest["source_mode"]}))


if __name__=="__main__":main()
