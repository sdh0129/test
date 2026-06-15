# SP3 CTR FLR Auto-Scheduler

자동차 부품 생산라인의 ALBP-HRC (Assembly Line Balancing Problem with Human-Robot Collaboration) 자동 시퀀싱 알고리즘.

## 파일 구성

```
sp3_scheduler/
├── sp3_scheduler.py    # 메인 실행 스크립트 (CLI)
├── algorithm.py        # E_ASA 알고리즘 본체
├── io_excel.py         # 엑셀 입출력
└── README.md           # 이 파일
```

## 논문 베이스

핵심 베이스: **Nourmohammadi et al. (2022)** Adaptive Simulated Annealing
보조 개선: **Zhang & Fujimura (2025)** Enhanced ASA 휴리스틱

차용한 요소:
- Nourmohammadi §5.1: Two-row solution representation
- Nourmohammadi §5.2: RPW-based roulette wheel construction
- Nourmohammadi §5.4: 4가지 이웃 탐색 (st-sw, st-mu, op-sw, op-mu)
- Nourmohammadi §5.5: Adaptive neighborhood selection
- Zhang §4.2.3: Inter-station task migration heuristic

## 설치

```bash
pip install openpyxl numpy
```

## 사용법

```bash
# 표준 실행
python sp3_scheduler.py 입력파일.xlsx --stations 13

# 빠른 테스트
python sp3_scheduler.py 입력파일.xlsx --stations 13 --quick

# 옵션 모두
python sp3_scheduler.py 입력파일.xlsx \
    --stations 13 \
    --humans 1 \
    --robots 1 \
    --output result.xlsx \
    --seed 42
```

## 입력 엑셀 스키마 (3시트)

### 1. BOM_Data
| Level | Parent_Part_No | Part_No | Part_Name | Qty | Variant |
|---|---|---|---|---|---|
| 1 | None | 65100-BS000 | PNL & MBR ASSY-CTR FLR | 1 | ALL |
| 2 | 65100-BS000 | 65111-BS000 | ... | 1 | ALL |

### 2. Task_Master
| Task_ID | Task_Name | Task_Type | CT | Output_Part_No | Variant |
|---|---|---|---|---|---|
| S06 | PLT 취출 & 정렬 | 2 | 9.0 | S06-ASSY | ALL |
| A06 | MARRAGE | 1 | 92.0 | A06-ASSY | ALL |

**Task_Type**: 0=인간전용, 1=로봇전용, 2=둘다가능

### 3. Precedence
| Predecessor_ID | Successor_ID |
|---|---|
| S06 | S06-HW |
| S06-HW | S07 |

## 출력 엑셀 (4시트)

1. **Summary** — 사이클타임, 작업자 수, 효율 등
2. **Schedule** — 각 task의 station/operator/start/end
3. **Gantt_Data** — 간트차트 시각화용 raw data
4. **Station_Metrics** — Station별 부하, 활용률, 유휴시간

## 알고리즘 개요

1. **위상정렬 + RPW 계산** — 각 task의 critical path 길이 계산
2. **초기해 생성** — RPW 기반 룰렛휠로 list scheduling
3. **SA 루프**:
   - 4가지 이웃 중 1개 선택 (adaptive)
   - 이웃 해 평가
   - Zhang 휴리스틱으로 개선 시도
   - Boltzmann 수락 기준
4. **온도 냉각 후 반복**, T < Tf까지

## SP3 케이스 결과

41개 공정, 13개 station, NH=NR=1 기준:
- **사이클타임**: 92초
- **라인 밸런스 효율**: 94.5%
- **병목**: A06 Marrage (60점 용접) — 분할 불가능

## 트러블슈팅

**Q: "초기해 생성 실패" 에러**
A: NS가 너무 작음. 가장 큰 task의 CT 이상이 되도록 NS 늘리거나 작업을 분할.

**Q: 결과 station 수가 입력보다 적게 나옴**
A: 정상. 알고리즘은 빈 station을 만들어 NS를 채우지 않고, 가능한 최소로 압축.

**Q: 같은 station에 인간/로봇이 동시에 들어가는데, 이건 collaboration인가?**
A: 부분적. 모델은 인간과 로봇이 병렬로 다른 task를 수행한다고 가정 (Nourmohammadi §3 case II). 동일 task를 같이 하는 joint task는 현재 미지원.
