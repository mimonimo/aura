"""두 묶음의 새 후보만 Label Studio에 게시. 승인·구버전 자동 복원 없음."""
import json
import fcntl
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from zzaimy.dataset.tracks import TRACKS

TITLES={"institutional_policy":"ZZAIMY 규정·발전계획 CoT",
        "program_retrieval":"ZZAIMY 사업 구조·검색 활용 CoT"}
CONFIG="""<View><Header value="학습 후보 — 두 묶음 분리"/>
<Header value="출처"/><Text name="source" value="$source"/>
<Header value="이전 대화·질문·근거 설명·답변"/><Text name="dialogue" value="$dialogue"/>
<Header value="검수 상태"/><Text name="state" value="$state"/>
<Choices name="decision" toName="dialogue" choice="single"><Choice value="적합"/><Choice value="재작성"/><Choice value="제외"/></Choices>
<TextArea name="review_note" toName="dialogue" placeholder="검수 의견"/></View>"""


def main():
    from dotenv import load_dotenv
    load_dotenv(".env.local")
    from zzaimy.app.db import Database
    from zzaimy.dataset.ls_client import LabelStudioClient
    db=Database(Path("data/platform/platform.db"))
    client=LabelStudioClient(db.get_setting("labelstudio_url"),db.get_setting("labelstudio_token"))
    result={}
    for track in TRACKS:
        pid=client.ensure_project(TITLES[track],label_config=CONFIG,
              description="연결 대화 단위 검수 후보. 자동 생성은 학습 승인이나 도구 실행 검증을 뜻하지 않습니다.")
        existing=set();task_by_sample={};page=1
        while True:
            response=client._req("GET",f"/api/tasks?project={pid}&page_size=100&page={page}")
            tasks=response.get("tasks",response.get("results",[])) if isinstance(response,dict) else response
            existing.update(t.get("data",{}).get("sample_id") for t in tasks)
            task_by_sample.update({t.get("data",{}).get("sample_id"):t for t in tasks})
            if len(tasks)<100:break
            page+=1
        pending=[]
        for path in sorted((Path("data/training/tracks")/track/"candidates").glob("*.json")):
            value=json.loads(path.read_text())
            review_path=path.parent.parent/"reviews"/path.name
            if review_path.exists():
                from zzaimy.dataset.tracks import key
                review=json.loads(review_path.read_text())
                if review.get("candidate_sha256")==key(value.get("candidate")) and review.get("decision")=="rewrite":
                    task=task_by_sample.get(value["job"])
                    if task:
                        data=dict(task["data"])
                        data["state"]="재작성 필요 · "+review["reason"]
                        data["_review"]=review
                        if data!=task["data"]:
                            client._req("PATCH",f"/api/tasks/{task['id']}",json={"data":data})
                    continue
            if value.get("status")!="candidate" or value["job"] in existing:continue
            candidate=value["candidate"]
            if candidate["track"]!=track or candidate.get("approved") is not False:raise ValueError("invalid_track")
            pending.append({"data":{"sample_id":value["job"],"track":track,
                "source":json.dumps(candidate["provenance"],ensure_ascii=False),
                "dialogue":json.dumps(candidate["messages"],ensure_ascii=False,indent=2),
                "state":"생성 후보 · 의미 검수 대기 · 검색 계획은 실제 도구 실행 기록 아님",
                "_candidate":candidate}})
        if pending:client._req("POST",f"/api/projects/{pid}/import",json=pending)
        result[track]={"project_id":pid,"uploaded_conversations":len(pending),"existing":len(existing)}
    dest=Path("data/training/tracks/labelstudio-status.json")
    dest.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result))


if __name__=="__main__":
    root=Path("data/training/tracks")
    root.mkdir(parents=True,exist_ok=True)
    with (root/"labelstudio.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        main()
