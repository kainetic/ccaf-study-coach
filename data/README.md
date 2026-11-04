# data/

| 파일 | 내용 | 커밋 |
|---|---|---|
| `raw/ccaf_exam_guide.pdf` | CCA-F 시험 가이드 원본 (v0.2) | X |
| `guide_chunks.jsonl` | 검색용 청크 62개 (`GuideChunk`) | X |
| `guide_bullets.jsonl` | TS 세부 항목 240개 (`GuideBullet`) | X |
| `quiz_pool.json` | 문제 은행 | O |
| `quiz_pool_manifest.json` | 생성 슬롯과 상태 | O |
| `study_log.json` | 풀이 기록 | O |

## 가이드 PDF 배치와 ingest

1. 시험 가이드 PDF를 `data/raw/ccaf_exam_guide.pdf`로 저장한다 (저장소에는 포함하지 않음).
2. 실행:

```powershell
python -m src.ingest --dry-run   # 파싱·개수 검증만 (Bedrock 호출 없음)
python -m src.ingest             # jsonl 2개 생성 + Chroma(./chroma_db, 컬렉션 ccaf_guide) 적재
```

청크가 62개, 세부 항목이 240개(도메인별 K/S 개수 포함)가 아니면 파일과 Chroma를 건드리지 않고 종료 코드 1로 멈춘다.
가이드 버전이 바뀌면 `src/ingest.py`의 기대값과 `docs/DESIGN.md` §2를 함께 갱신해야 한다.
