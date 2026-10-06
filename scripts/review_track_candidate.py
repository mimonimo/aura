"""생성본을 보존하고 직접 검수한 재작성 사유를 별도 기록한다."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from zzaimy.dataset.tracks import TRACKS, key


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track",choices=TRACKS,required=True)
    parser.add_argument("--job",required=True)
    parser.add_argument("--reason",required=True)
    args=parser.parse_args()
    if len(args.job)!=64 or any(c not in "0123456789abcdef" for c in args.job):
        parser.error("invalid job")
    root=Path("data/training/tracks")/args.track
    value=json.loads((root/"candidates"/(args.job+".json")).read_text())
    review={"candidate_sha256":key(value["candidate"]),"decision":"rewrite",
            "reason":args.reason,"reviewer":"Codex","created_at":time.time()}
    dest=root/"reviews"/(args.job+".json")
    dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():
        raise RuntimeError("review_exists")
    dest.write_text(json.dumps(review,ensure_ascii=False,indent=2))
    print(json.dumps(review,ensure_ascii=False))


if __name__=="__main__":
    main()
