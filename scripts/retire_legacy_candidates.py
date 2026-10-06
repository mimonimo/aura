"""옛 CoT 활성 경로를 복구 가능한 보관함으로 이동. LS 삭제는 검증 백업 후에만 수행."""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(".env.local")
    from zzaimy.app.db import Database
    from zzaimy.dataset.ls_client import LabelStudioClient
    db=Database(Path("data/platform/platform.db"))
    client=LabelStudioClient(db.get_setting("labelstudio_url"),db.get_setting("labelstudio_token"))
    expected={5:"ZZAIMY 근거 기반 문답 검수",2:"ZZAIMY 실문서 절 작성"}
    def tasks(pid):
        result=[];page=1
        while True:
            response=client._req("GET",f"/api/tasks?project={pid}&page_size=100&page={page}")
            batch=response.get("tasks",response.get("results",[])) if isinstance(response,dict) else response
            result.extend(batch)
            if len(batch)<100:return result
            page+=1
    snapshots=[]
    for pid,title in expected.items():
        project=client._req("GET",f"/api/projects/{pid}")
        if project["title"] != title:raise ValueError("project_changed")
        snapshots.append({"project":project,"tasks":tasks(pid)})
    root=Path("data/training")
    exact={"generated-candidates","candidate-reviews","real_pairs.jsonl","real_sft.jsonl","real_dpo.jsonl",
           "real_report.md","tree_cot_alpaca.jsonl","tree_cot_sft.jsonl","tree_cot_pairs.jsonl","tree_cot_report.md",
           "candidate-publish-status.json"}
    selected=[p for p in root.iterdir() if p.name in exact or p.name.startswith(("authored-qa-","ai-review-","question-tree-review-"))]
    print(json.dumps({"paths":[p.name for p in selected],"ls":[{"id":s["project"]["id"],"tasks":len(s["tasks"])} for s in snapshots]}),flush=True)
    if not args.apply:return
    target=root/"retired"/datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target.mkdir(parents=True,mode=0o700)
    for snapshot in snapshots:
        pid=snapshot["project"]["id"]
        dest=target/f"labelstudio-{pid}.json"
        encoded=json.dumps(snapshot,ensure_ascii=False,sort_keys=True).encode()
        dest.write_bytes(encoded);dest.chmod(0o600)
        if dest.read_bytes()!=encoded:raise ValueError("backup_verification_failed")
        dest.with_suffix(".sha256").write_text(hashlib.sha256(encoded).hexdigest())
        if tasks(pid)!=snapshot["tasks"]:raise ValueError("tasks_changed_during_backup")
    (target/"restore.json").write_text(json.dumps({"original_root":str(root),"paths":[p.name for p in selected],
        "note":"파일은 원래 경로로 이동해 복구, Label Studio는 project 설정과 tasks 백업으로 재생성. 새 학습에 자동 혼합하지 않음."},ensure_ascii=False,indent=2))
    for path in selected:
        path.rename(target/path.name)
    (root/"active-tracks.json").write_text(json.dumps({"tracks":["institutional_policy","program_retrieval"],
        "legacy_retired":str(target),"legacy_generation_enabled":False},indent=2))
    for snapshot in snapshots:
        client._req("DELETE",f"/api/projects/{snapshot['project']['id']}")
    print(json.dumps({"retired_to":str(target),"removed_projects":list(expected),"restorable":True}),flush=True)


if __name__=="__main__":main()
