"""임베딩 학습 1-step 스모크 테스트 (W1-W2 TASK-03).

sentence-transformers 계열 대조학습 역전파가 aarch64 + sm_121에서 도는지만
확인한다. 학습 자체가 목적이 아니며, 데이터는 아래 합성 4쌍뿐이다.

실행: .venv-train/bin/python scripts/20_embed_smoke.py
"""

from __future__ import annotations

from datasets import Dataset
from sentence_transformers import (
    SentenceTransformer,
    SentenceTransformerTrainer,
    SentenceTransformerTrainingArguments,
)
from sentence_transformers.losses import MultipleNegativesRankingLoss

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

SYNTHETIC_PAIRS = {
    "anchor": [
        "교육과정 개편의 필요성은 무엇인가",
        "성과지표의 정의는",
        "사업 예산 편성 기준",
        "프로그램 운영 실적 정리 방법",
    ],
    "positive": [
        "교육과정 개편이 필요한 이유를 설명한 합성 문단",
        "성과지표를 정의한 합성 문단",
        "예산 편성 기준을 설명한 합성 문단",
        "운영 실적 정리 방법을 설명한 합성 문단",
    ],
}


def main() -> None:
    model = SentenceTransformer(MODEL)
    trainer = SentenceTransformerTrainer(
        model=model,
        args=SentenceTransformerTrainingArguments(
            output_dir="/tmp/zzaimy-embed-smoke",
            max_steps=1,
            per_device_train_batch_size=4,
            logging_steps=1,
            report_to="none",
        ),
        train_dataset=Dataset.from_dict(SYNTHETIC_PAIRS),
        loss=MultipleNegativesRankingLoss(model),
    )
    result = trainer.train()
    print(f"SMOKE_OK loss={result.training_loss:.4f} steps={result.global_step}")


if __name__ == "__main__":
    main()
