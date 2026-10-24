# Abstract

Title: AURA: An On-Premise sLLM Platform and Document Agent for University Administrative Work (영문 제목은 표지 확정 후 맞춘다)

University administration runs on documents such as grant announcements, project plans, performance reports and regulations.
These documents contain personal and institutional information, so they cannot be sent to commercial cloud AI services and staff search
past files by hand. This project builds AURA, a document processing platform that runs entirely on campus hardware: a CPU-only operations
server, two NVIDIA Jetson AGX Thor devices for model serving, and an NVIDIA DGX for storage, batch processing and training.

AURA reads digital PDFs, scanned documents, photos, HWP/HWPX and office files while preserving tables and detects personal information with Presidio
and Korean-specific recognizers. Retrieval combines Kiwi-based lexical search and dense search with
reciprocal rank fusion, using fine-tuned KURE-v1 and bge-reranker-v2-m3 models. An ontology of programs, years, documents and sections,
validated with OWL and SHACL, forms a knowledge graph of 415,834 nodes and 583,557 edges. The open-weight model Qwen3.8-27B, served with vLLM in NVFP4,
reads each question into ontology terms; graph scoping raised the share of section questions whose correct document appears in the top five from 18% with
full-text retrieval to 70%. Answers cite only the evidence passed to the model, and a deterministic checker marks numbers that are not in the evidence.
A document agent selects tools to draft and revise sections, fills the original HWPX form without breaking its layout, and co-edits
fields with people on a web HWP editor (median latency 221 ms per character).

The system produces drafts that staff review, not final submissions. A supervised fine-tuned writer model improved in-format metrics but broke the agent
workflow and was not adopted; we report this result with the evaluation procedure.

Keywords: on-premise LLM, retrieval-augmented generation, knowledge graph, ontology, document agent, Korean administrative documents, HWPX, co-editing
