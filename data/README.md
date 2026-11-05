# data/

| 파일 | 내용 | 커밋 |
|---|---|---|
| `raw/ccaf_exam_guide.pdf` | CCA-F 시험 가이드 원본 (v0.2) | X |
| `guide_chunks.jsonl` | 검색용 청크 62개 (`GuideChunk`) | X |
| `guide_bullets.jsonl` | TS 세부 항목 240개 (`GuideBullet`) | X |
| `quiz_pool.json` | 문제 은행 (`QuizItem`) 284문항: approved 266 · rejected 16 · duplicate 2. 출제는 approved만 하고, 탈락·중복 문항은 기록으로 남김 | O |
| `quiz_pool_manifest.json` | 생성 슬롯 284개와 상태 (`QuizManifest`, 재개·보충용) | O |
| `study_log.json` | 풀이 기록 (`StudyLog`). 시드 사용자 `demo_heavy`(61회)·`demo_new`(기록 없음), `scripts/seed_study_log.py`로 재생성 | O |

X 항목은 가이드 원문이거나 그 파생물이라 비공개 저장소여도 커밋하지 않는다. 클론하면 가이드 원문을 재배포하는 셈이 되기 때문이다. 저장소 루트의 `chroma_db/`도 같은 이유로 커밋하지 않는다. 모두 아래 ingest로 다시 만든다 (`docs/DESIGN.md` §2-4).

`study_log.json`은 API 서버(웹 UI 포함)와 `evaluation/smoke_p0.py`가 직접 기록한다. 커밋된 시드 기록을 바꾸지 않으려면 환경 변수 `CCAF_STUDY_LOG`로 임시 사본을 지정한다 (루트 README "study_log 보호").

## 가이드 PDF 배치와 ingest

1. 공개된 공식 시험 가이드(v0.2, 2026-06-30) PDF를 `data/raw/ccaf_exam_guide.pdf`로 저장한다 (저장소에는 포함하지 않음).
2. 실행:

```powershell
python -m src.ingest --dry-run   # 파싱·검증만 (Bedrock 호출·파일 쓰기 없음)
python -m src.ingest             # jsonl 2개 생성 + Chroma(./chroma_db, 컬렉션 ccaf_guide) 적재
```

- 검증: 문서 유형별 청크 개수(합계 62)와 doc_id 목록, 도메인별 세부 항목 K/S 개수(합계 240)가 기대값과 다르면 jsonl과 Chroma를 건드리지 않고 종료 코드 1로 멈춘다.
- 다시 실행해도 된다. 기존 Chroma 컬렉션을 지우고 새로 만들어 청크가 중복되지 않는다.
- 문제 풀(`quiz_pool.json`)은 다시 만들 필요가 없다. ingest가 끝나면 바로 출제·채점할 수 있다.

가이드 버전이 바뀌면 함께 갱신할 것: `src/config.py`의 `GUIDE_VERSION`(다르면 경고만 내고 계속 진행), `src/ingest.py`의 기대 개수(`EXPECTED_DOC_TYPES`·`EXPECTED_BULLETS`), `docs/DESIGN.md` §2. 세부 항목이 바뀌므로 문제 풀도 `scripts/build_quiz_pool.py`로 다시 만들어야 한다 (DESIGN §5-2).
