"""
SP3 CTR FLR Auto-Scheduler
==========================
HRC ALBP-2 자동 시퀀싱 — 사이클타임 + 작업자 수 최소화

논문 기반:
- Nourmohammadi et al. (2022): Adaptive Simulated Annealing (ASA)
- Zhang & Fujimura (2025): Enhanced ASA (E_ASA) with heuristic mechanisms

입력: 3시트 엑셀 (BOM_Data, Task_Master, Precedence)
출력: station-operator-task 할당 + 간트 차트 데이터

사용법:
    python sp3_scheduler.py <입력파일.xlsx> [옵션]

옵션:
    --stations N        스테이션 수 (필수)
    --humans N          스테이션당 최대 인간 작업자 (기본 1)
    --robots N          스테이션당 최대 로봇 (기본 1)
    --output FILE       결과 저장 파일 (기본 result.xlsx)
    --seed N            랜덤 시드 (재현용)
    --quick             빠른 실행 (반복 횟수 축소)

필요 패키지: openpyxl, numpy
설치: pip install openpyxl numpy
"""

import sys
import argparse
import random
import time
from pathlib import Path

# 의존성 체크
try:
    import openpyxl
    import numpy as np
except ImportError as e:
    print(f"ERROR: 필요한 패키지가 없습니다 ({e})")
    print("설치: pip install openpyxl numpy")
    sys.exit(1)

from io_excel import read_input, write_result, print_summary
from algorithm import EnhancedASA


def main():
    parser = argparse.ArgumentParser(
        description='SP3 CTR FLR 자동 시퀀싱 (E_ASA)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='예시: python sp3_scheduler.py SP3_input.xlsx --stations 13 --humans 1 --robots 1'
    )
    parser.add_argument('input_file', help='입력 엑셀 파일')
    parser.add_argument('--stations', type=int, required=True,
                        help='스테이션 수 (NS)')
    parser.add_argument('--humans', type=int, default=1,
                        help='스테이션당 최대 인간 수 (기본 1)')
    parser.add_argument('--robots', type=int, default=1,
                        help='스테이션당 최대 로봇 수 (기본 1)')
    parser.add_argument('--output', default='result.xlsx',
                        help='출력 파일 (기본 result.xlsx)')
    parser.add_argument('--seed', type=int, default=42,
                        help='랜덤 시드 (기본 42)')
    parser.add_argument('--quick', action='store_true',
                        help='빠른 실행 (테스트용, 품질 낮음)')
    args = parser.parse_args()

    # 시드 고정 (재현 가능성)
    random.seed(args.seed)
    np.random.seed(args.seed)

    # 1) 입력 읽기 + 검증
    print(f"\n{'='*70}")
    print(f"  SP3 CTR FLR 자동 시퀀싱 (Enhanced Adaptive SA)")
    print(f"{'='*70}\n")

    if not Path(args.input_file).exists():
        print(f"ERROR: 입력 파일 없음: {args.input_file}")
        sys.exit(1)

    print(f"[1/3] 입력 파일 읽는 중: {args.input_file}")
    try:
        data = read_input(args.input_file)
    except Exception as e:
        print(f"ERROR: 입력 파일 읽기 실패 — {e}")
        sys.exit(1)

    print(f"      BOM 항목: {len(data['bom'])}개")
    print(f"      공정 (Task): {len(data['tasks'])}개")
    print(f"      선후관계: {len(data['edges'])}쌍")

    # 무결성 검증
    errors = validate(data, args.stations)
    if errors:
        print(f"\n[ERROR] 무결성 검증 실패:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print(f"      [OK] 무결성 검증 통과")

    # 2) E_ASA 실행
    print(f"\n[2/3] 알고리즘 실행 중...")
    print(f"      NS={args.stations}, NH={args.humans}, NR={args.robots}")
    if args.quick:
        params = {'T0': 50, 'Tf': 1.0, 'cr': 0.97, 'maxIT': 30}
        print(f"      [Quick] Quick 모드 (낮은 정확도, 빠른 속도)")
    else:
        params = {'T0': 100, 'Tf': 0.1, 'cr': 0.99, 'maxIT': 100}
        print(f"      [Standard] 표준 모드")

    algo = EnhancedASA(
        tasks=data['tasks'],
        edges=data['edges'],
        ns=args.stations,
        nh=args.humans,
        nr=args.robots,
        **params,
    )

    t_start = time.time()
    best = algo.run(verbose=True)
    elapsed = time.time() - t_start

    print(f"\n      [OK] 완료 (소요시간 {elapsed:.1f}초)")

    # 3) 결과 저장 + 요약
    print(f"\n[3/3] 결과 저장 중...")
    write_result(args.output, data, best, args)
    print(f"      [OK] {args.output}")

    print(f"\n{'='*70}")
    print(f"  결과 요약")
    print(f"{'='*70}")
    print_summary(best, data['tasks'])

    print(f"\n[Output] 상세 결과: {args.output}\n")


def validate(data, ns):
    """입력 무결성 검증. 에러 리스트 반환 (비어있으면 OK)"""
    errors = []
    bom_pns = {r['part_no'] for r in data['bom']}
    task_ids = set(data['tasks'].keys())

    # BOM parent FK
    for r in data['bom']:
        pp = r.get('parent_pn')
        if pp not in (None, 'None', '') and pp not in bom_pns:
            errors.append(f"BOM: Parent_Part_No '{pp}' (부품 {r['part_no']})가 BOM에 없음")

    # Precedence FK
    for p, s in data['edges']:
        if p not in task_ids:
            errors.append(f"Precedence: '{p}'가 Task_Master에 없음")
        if s not in task_ids:
            errors.append(f"Precedence: '{s}'가 Task_Master에 없음")

    # CT > 0
    zero_ct = [tid for tid, t in data['tasks'].items() if t['ct_human'] <= 0 and t['ct_robot'] <= 0]
    if zero_ct:
        errors.append(f"CT 0인 task: {zero_ct[:5]}{'...' if len(zero_ct) > 5 else ''}")

    # Task_Type 범위
    bad_type = [tid for tid, t in data['tasks'].items() if t['task_type'] not in (0, 1, 2)]
    if bad_type:
        errors.append(f"Task_Type가 0/1/2가 아님: {bad_type[:5]}")

    # NS 합리성 체크
    total_ct = sum(min(t['ct_human'] or 1e9, t['ct_robot'] or 1e9)
                   for t in data['tasks'].values())
    if ns < 1:
        errors.append(f"NS={ns} 는 최소 1 이상이어야 함")

    # DAG 사이클 검출
    from collections import defaultdict, deque
    adj = defaultdict(list)
    indeg = {t: 0 for t in task_ids}
    for p, s in data['edges']:
        if p in task_ids and s in task_ids:
            adj[p].append(s)
            indeg[s] += 1
    q = deque([t for t in task_ids if indeg[t] == 0])
    visited = 0
    while q:
        n = q.popleft()
        visited += 1
        for x in adj[n]:
            indeg[x] -= 1
            if indeg[x] == 0:
                q.append(x)
    if visited != len(task_ids):
        errors.append(f"Precedence에 사이클 있음 ({visited}/{len(task_ids)} 방문됨)")

    return errors


if __name__ == '__main__':
    main()
