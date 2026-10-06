"""로컬 원문 명세에서 두 묶음 후보 생성. 외부 수집·학습·자동 승인 없음."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from zzaimy.dataset.tracks import TRACKS, SYSTEM, INSTRUCTIONS, build, key
from zzaimy.dataset.privacy import protect_candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--max-jobs", type=int, default=10)
    parser.add_argument("--windows-per-source", type=int, default=2)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    if manifest["track"] not in TRACKS or args.max_jobs < 1 or args.windows_per_source < 1:
        parser.error("invalid track or limit")
    from dotenv import load_dotenv
    load_dotenv(".env.local")
    from zzaimy.generate import model_config
    from openai import OpenAI
    cfg = model_config.current("review")
    if not cfg["configured"] or cfg.get("external") or cfg.get("kind") != "vllm":
        raise RuntimeError("internal_model_required")
    client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"], timeout=150, max_retries=0)
    out = Path("data/training/tracks") / manifest["track"] / "candidates"
    out.mkdir(parents=True, exist_ok=True)
    jobs = 0
    for source in manifest["sources"]:
        source_jobs = 0
        text = Path(source["text_path"]).read_text()
        if key(text) != source["text_sha256"]:
            raise ValueError("source_changed")
        # 문서 경계와 원문 위치를 보존하고, 한 번에 보내는 분량을 제한한다.
        for offset in range(0, len(text), 7000):
            window = protect_candidate(text[offset:offset + 7000])
            if len(window.strip()) < 300:
                continue
            provenance = {k: source[k] for k in ("id", "title", "text_sha256")}
            provenance["offset"] = offset
            job = key([manifest["track"], provenance, SYSTEM, INSTRUCTIONS[manifest["track"]]])
            dest = out / (job + ".json")
            if dest.exists():
                continue
            if source_jobs >= args.windows_per_source:
                break
            if jobs >= args.max_jobs:
                return
            jobs += 1
            source_jobs += 1
            if not args.apply:
                print(json.dumps({"job": job, "track": manifest["track"]}), flush=True)
                continue
            result = {"job": job, "track": manifest["track"], "approved": False, "provenance": provenance}
            try:
                response = client.chat.completions.create(
                    model=cfg["model"], temperature=0.2, max_tokens=4500,
                    response_format={"type": "json_object"},
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                    messages=[{"role":"system", "content":SYSTEM + INSTRUCTIONS[manifest["track"]]},
                              {"role":"user", "content":window}])
                choice = response.choices[0]
                if choice.finish_reason == "length":
                    raise ValueError("truncated_output")
                parsed = json.loads(choice.message.content)
                result["candidate"] = build(manifest["track"], parsed, window, provenance)
                result["status"] = "candidate"
            except Exception as exc:
                result.update(status="held", error_type=type(exc).__name__)
            result["created_at"] = time.time()
            temporary = dest.with_suffix(".tmp")
            temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2))
            temporary.replace(dest)
            print(json.dumps({"track":manifest["track"],"job":job,"status":result["status"]}),flush=True)


if __name__ == "__main__":
    main()
