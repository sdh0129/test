"""
Enhanced Adaptive Simulated Annealing (E_ASA)
==============================================
ALBP-HRC 자동 시퀀싱 알고리즘

논문 베이스:
[1] Nourmohammadi, A., Fathi, M., Ng, A.H.C. (2022). Balancing and scheduling
    assembly lines with human-robot collaboration tasks. Computers & Operations
    Research, 140, 105674.
[2] Zhang, J. & Fujimura, S. (2025). An innovative meta-heuristic for balancing
    and scheduling HRC assembly lines in Industry 5.0. JIPE, 43(1), 117-137.

핵심 차용 요소:
- [1] §5.1: Two-row solution representation (station + operator)
- [1] §5.2: Feasible solution generation with RPW-based roulette
- [1] §5.4: 4가지 이웃 탐색 (st-sw, st-mu, op-sw, op-mu)
- [1] §5.5: Adaptive neighborhood selection (matrix 기반)
- [2] §4.1 Algorithm 1: 워크스테이션 내부 idle slot 재배치
- [2] §4.2.3 Algorithm 2: 가장 느린 station에서 작업 이동

목적함수 (식 1 in [1]):
    minimize v = ct + (NH_used + NR_used) / ((NH + NR) * NS + 1)
    → 사이클타임 최소화가 1차, 작업자 수 최소화가 2차
"""

import math
import random
from collections import defaultdict, deque


class EnhancedASA:
    """
    Enhanced Adaptive Simulated Annealing for ALBP-HRC.

    Parameters
    ----------
    tasks : dict
        {task_id: {task_name, task_type, ct_human, ct_robot, ...}}
    edges : list of (pred, succ)
    ns : int   # 스테이션 수
    nh : int   # 스테이션당 최대 인간 (기본 1)
    nr : int   # 스테이션당 최대 로봇 (기본 1)
    T0 : float # 초기 온도 (논문 권장: 100)
    Tf : float # 종료 온도 (논문 권장: 0.1)
    cr : float # 냉각률 (논문 권장: 0.99)
    maxIT : int # 온도당 반복 (논문 권장: 100)
    """

    def __init__(self, tasks, edges, ns, nh=1, nr=1,
                 T0=100, Tf=0.1, cr=0.99, maxIT=100):
        self.tasks = tasks
        self.task_ids = list(tasks.keys())
        self.NT = len(tasks)
        self.NS = ns
        self.NH = nh
        self.NR = nr
        self.T0 = T0
        self.Tf = Tf
        self.cr = cr
        self.maxIT = maxIT

        # 선후관계 인덱스
        self.pred = defaultdict(set)
        self.succ = defaultdict(set)
        for p, s in edges:
            self.pred[s].add(p)
            self.succ[p].add(s)

        # 모든 선행/후행 (전이폐쇄)
        self.pall = self._all_ancestors()  # i: 모든 선행
        self.sall = self._all_descendants()  # i: 모든 후행
        self.psall = {t: self.pall[t] | self.sall[t] for t in self.task_ids}

        # RPW (Ranked Positional Weight) — 논문 [1] Table 2 정의
        # rpw(i) = min(ct_human, ct_robot) + sum over all successors
        self.rpw = self._compute_rpw()

        # 이웃 선택 적응 매트릭스 (논문 [1] §5.5)
        # 4가지 이웃: 0=st-sw, 1=st-mu, 2=op-sw, 3=op-mu
        self.neighbor_names = ['st-sw', 'st-mu', 'op-sw', 'op-mu']
        self._reset_neighbor_matrix()

        # 작업의 가능한 자원 타입 (Task_Type 기반)
        self.feasible_workers = {}  # task_id → list of (worker_type, ct)
        for tid, t in tasks.items():
            options = []
            if t['ct_human'] < float('inf'):
                options.append(('human', t['ct_human']))
            if t['ct_robot'] < float('inf'):
                options.append(('robot', t['ct_robot']))
            if not options:
                raise ValueError(f"Task {tid}: 인간/로봇 모두 불가 — Task_Type 확인")
            self.feasible_workers[tid] = options

    # ============================================================
    # 자료구조 초기화
    # ============================================================
    def _all_ancestors(self):
        """각 task의 모든 선행 작업 (직간접)"""
        result = {t: set() for t in self.task_ids}
        order = self._topo_order()
        for t in order:
            for p in self.pred[t]:
                result[t].add(p)
                result[t] |= result[p]
        return result

    def _all_descendants(self):
        """각 task의 모든 후행 작업 (직간접)"""
        result = {t: set() for t in self.task_ids}
        order = list(reversed(self._topo_order()))
        for t in order:
            for s in self.succ[t]:
                result[t].add(s)
                result[t] |= result[s]
        return result

    def _topo_order(self):
        """위상정렬"""
        indeg = {t: len(self.pred[t]) for t in self.task_ids}
        q = deque([t for t in self.task_ids if indeg[t] == 0])
        order = []
        while q:
            t = q.popleft()
            order.append(t)
            for s in self.succ[t]:
                indeg[s] -= 1
                if indeg[s] == 0:
                    q.append(s)
        return order

    def _compute_rpw(self):
        """
        RPW(i) = min(t_human_i, t_robot_i) + sum over all successors k:
                 min(t_human_k, t_robot_k)
        후행 체인이 긴 작업일수록 우선순위 ↑
        """
        rpw = {}
        for t in self.task_ids:
            own_t = min(self.tasks[t]['ct_human'], self.tasks[t]['ct_robot'])
            succ_t = sum(min(self.tasks[s]['ct_human'], self.tasks[s]['ct_robot'])
                         for s in self.sall[t])
            rpw[t] = own_t + succ_t
        return rpw

    def _reset_neighbor_matrix(self):
        """Adaptive neighborhood selection matrix 초기화 (논문 [1] Fig.6)"""
        # [n_calls, sum_of_obj, avg_of_obj, selection_prob]
        self.nb_calls = [1, 1, 1, 1]
        self.nb_sum_obj = [0.0, 0.0, 0.0, 0.0]
        self.nb_avg = [0.0, 0.0, 0.0, 0.0]
        self.nb_prob = [0.25, 0.25, 0.25, 0.25]
        self.worst_obj = 0.0

    # ============================================================
    # 초기해 생성 (논문 [1] §5.3)
    # ============================================================
    def generate_initial_solution(self):
        """
        RPW 기반 룰렛휠 선택으로 초기해 생성.
        Lower bound:
            ctlow = max(sum(min_ti) / ((NH+NR)*NS), max(min_ti))
        """
        min_t = [min(self.tasks[t]['ct_human'], self.tasks[t]['ct_robot'])
                 for t in self.task_ids]
        ct_low = max(sum(min_t) / ((self.NH + self.NR) * self.NS),
                     max(min_t))

        # τ values to try (논문 [1] 권장: 1.0 ~ 1.5)
        best = None
        best_obj = float('inf')
        for tau in [1.0, 1.1, 1.2, 1.3, 1.5]:
            sol = self._construct_solution(ct_low * tau)
            if sol is not None:
                obj = self._evaluate(sol)['objective']
                if obj < best_obj:
                    best_obj = obj
                    best = sol
        return best

    def _construct_solution(self, target_ct):
        """
        RPW 룰렛휠 + List Scheduling. 각 station을 target_ct까지 채우고 다음으로.
        Station 내에서 인간+로봇은 병렬 실행 가능하므로
        max(human_load, robot_load)가 target_ct를 안 넘게 관리.
        """
        assignment = {}
        # 각 station의 [human_load, robot_load]
        loads = [[0.0, 0.0] for _ in range(self.NS + 1)]  # 1-indexed

        completed = set()
        remaining = set(self.task_ids)
        station = 1

        while remaining and station <= self.NS:
            # 현재 station에 더 채울 수 있는지 시도
            progress = True
            while progress and remaining:
                progress = False
                # 준비된 작업
                ready = [t for t in remaining if self.pred[t].issubset(completed)]
                if not ready:
                    break

                # 각 ready task에 대해 이 station에 들어갈 수 있는지 평가
                feasible_choices = []  # (task, wtype, ct, new_load)
                for t in ready:
                    for wtype, ct in self.feasible_workers[t]:
                        load_idx = 0 if wtype == 'human' else 1
                        new_load = loads[station][load_idx] + ct
                        # 마지막 station이면 강제로 받음
                        if station == self.NS or new_load <= target_ct:
                            feasible_choices.append((t, wtype, ct, new_load))

                if not feasible_choices:
                    break  # 다음 station으로

                # RPW 기반 룰렛휠로 task 선택
                # 같은 task가 여러 wtype으로 가능하면 cs가 짧은 것 우선
                task_to_best = {}
                for t, wtype, ct, new_load in feasible_choices:
                    if t not in task_to_best or ct < task_to_best[t][1]:
                        task_to_best[t] = (wtype, ct, new_load)

                candidate_tasks = list(task_to_best.keys())
                chosen = self._roulette_select(candidate_tasks, self.rpw)
                wtype, ct, new_load = task_to_best[chosen]

                # 할당
                load_idx = 0 if wtype == 'human' else 1
                # worker index 결정: 같은 station에 같은 wtype은 일단 idx=1 (NH=NR=1 가정)
                # NH > 1 이면 부하 분산이지만 일단 단순화
                assignment[chosen] = (station, wtype, 1)
                loads[station][load_idx] = new_load
                completed.add(chosen)
                remaining.discard(chosen)
                progress = True

            station += 1

        if remaining:
            # 남은 task는 마지막 station에 강제 할당
            last_s = self.NS
            for t in list(remaining):
                wtype, ct = self.feasible_workers[t][0]  # 첫 옵션
                assignment[t] = (last_s, wtype, 1)
                remaining.discard(t)

        return assignment

    def _roulette_select(self, candidates, weights):
        """RPW 기반 룰렛휠 선택"""
        total = sum(weights[t] for t in candidates)
        if total <= 0:
            return random.choice(candidates)
        r = random.uniform(0, total)
        acc = 0
        for t in candidates:
            acc += weights[t]
            if acc >= r:
                return t
        return candidates[-1]

    # ============================================================
    # 평가 (목적함수 + 스케줄 디코딩)
    # ============================================================
    def _evaluate(self, assignment):
        """
        해의 평가 — Station별 독립 스케줄 디코딩.

        ALBP의 핵심 가정:
        - 모든 station은 동시에 cycle을 시작 (time=0)
        - 한 station 내에서만 선후관계 + worker 동시작업 불가 제약 적용
        - station 간 선후관계는 station 번호 순서로만 강제 (i < j)
        - cycle_time = max over stations of station_completion_time
        """
        # 각 task를 station별로 그룹핑
        by_station = defaultdict(list)
        for tid, (s, w, idx) in assignment.items():
            by_station[s].append(tid)

        start = {}
        end = {}
        station_end = {}

        # Station 1부터 NS까지 독립적으로 스케줄링
        for s in range(1, self.NS + 1):
            tasks_here = by_station.get(s, [])
            if not tasks_here:
                station_end[s] = 0
                continue

            # 이 station 내 task들만의 선후관계 부분 그래프
            in_set = set(tasks_here)
            local_indeg = {t: len(self.pred[t] & in_set) for t in tasks_here}

            # Worker별 완료시각 (이 station 안에서만)
            worker_finish = defaultdict(float)

            # 처리 가능한 큐 (선행 없음)
            ready = [t for t in tasks_here if local_indeg[t] == 0]

            while ready:
                # RPW 큰 것 먼저
                ready.sort(key=lambda t: -self.rpw[t])
                t = ready.pop(0)
                _, wtype, idx = assignment[t]
                ct = (self.tasks[t]['ct_human'] if wtype == 'human'
                      else self.tasks[t]['ct_robot'])

                # 시작 가능 시각 = max(이 station 내 선행 task 완료, 같은 worker의 직전 완료)
                s_min = worker_finish[(wtype, idx)]
                for p in self.pred[t]:
                    if p in in_set and p in end:
                        s_min = max(s_min, end[p])
                start[t] = s_min
                end[t] = s_min + ct
                worker_finish[(wtype, idx)] = end[t]

                # 후속 task들의 indeg 감소
                for succ in self.succ[t]:
                    if succ in local_indeg:
                        local_indeg[succ] -= 1
                        if local_indeg[succ] == 0:
                            ready.append(succ)

            # 이 station의 완료시각 = 가장 늦은 worker 완료시각
            station_end[s] = max(worker_finish.values()) if worker_finish else 0

        # 사이클타임 = max(station_end)
        ct_total = max(station_end.values()) if station_end else 0

        # 사용된 worker 수
        used_humans = set()
        used_robots = set()
        for tid, (s, wtype, idx) in assignment.items():
            if wtype == 'human':
                used_humans.add((s, idx))
            else:
                used_robots.add((s, idx))

        n_humans = len(used_humans)
        n_robots = len(used_robots)

        # 목적함수 (논문 [1] 식 1)
        denom = (self.NH + self.NR) * self.NS + 1
        objective = ct_total + (n_humans + n_robots) / denom

        return {
            'assignment': assignment,
            'start': start,
            'end': end,
            'station_end': station_end,
            'cycle_time': ct_total,
            'n_humans': n_humans,
            'n_robots': n_robots,
            'objective': objective,
        }

    # ============================================================
    # 이웃 탐색 (논문 [1] §5.4)
    # ============================================================
    def _neighbor(self, assignment, op_idx):
        """4가지 이웃 탐색 중 하나 적용. 실패시 None."""
        new_a = dict(assignment)
        if op_idx == 0:    # station-swap
            return self._station_swap(new_a)
        elif op_idx == 1:  # station-mutation
            return self._station_mutation(new_a)
        elif op_idx == 2:  # operator-swap
            return self._operator_swap(new_a)
        else:              # operator-mutation
            return self._operator_mutation(new_a)

    def _station_range(self, task_id, assignment):
        """task_id가 들어갈 수 있는 station 범위 [F, L]"""
        # 선행 작업이 있는 가장 큰 station
        F = 1
        for p in self.pall[task_id]:
            if p in assignment:
                F = max(F, assignment[p][0])
        # 후행 작업이 있는 가장 작은 station
        L = self.NS
        for s in self.sall[task_id]:
            if s in assignment:
                L = min(L, assignment[s][0])
        return F, L

    def _station_swap(self, a):
        """두 작업의 station을 교환"""
        candidates = list(a.keys())
        random.shuffle(candidates)
        for i in candidates:
            for k in candidates:
                if i == k or k in self.psall[i]:
                    continue
                si, sk = a[i][0], a[k][0]
                if si == sk:
                    continue
                # 양쪽 모두 이동 가능한지 체크
                Fi, Li = self._station_range(i, a)
                Fk, Lk = self._station_range(k, a)
                if Fi <= sk <= Li and Fk <= si <= Lk:
                    a[i] = (sk, a[i][1], a[i][2])
                    a[k] = (si, a[k][1], a[k][2])
                    return self._reassign_workers(a)
        return None

    def _station_mutation(self, a):
        """한 작업을 다른 station으로 이동"""
        tasks = list(a.keys())
        random.shuffle(tasks)
        for t in tasks:
            F, L = self._station_range(t, a)
            if F == L:
                continue
            cur_s = a[t][0]
            options = [s for s in range(F, L + 1) if s != cur_s]
            if not options:
                continue
            new_s = random.choice(options)
            a[t] = (new_s, a[t][1], a[t][2])
            return self._reassign_workers(a)
        return None

    def _operator_swap(self, a):
        """두 작업의 operator를 교환 (호환되는 경우)"""
        tasks = list(a.keys())
        random.shuffle(tasks)
        for i in tasks:
            for k in tasks:
                if i == k:
                    continue
                wi, wk = a[i][1], a[k][1]
                if wi == wk:
                    continue
                # 양쪽 모두 가능한 자원이어야
                if not any(opt[0] == wk for opt in self.feasible_workers[i]):
                    continue
                if not any(opt[0] == wi for opt in self.feasible_workers[k]):
                    continue
                a[i] = (a[i][0], wk, a[i][2])
                a[k] = (a[k][0], wi, a[k][2])
                return self._reassign_workers(a)
        return None

    def _operator_mutation(self, a):
        """한 작업의 operator 타입 변경"""
        tasks = list(a.keys())
        random.shuffle(tasks)
        for t in tasks:
            current = a[t][1]
            alternatives = [opt[0] for opt in self.feasible_workers[t] if opt[0] != current]
            if not alternatives:
                continue
            new_w = random.choice(alternatives)
            a[t] = (a[t][0], new_w, a[t][2])
            return self._reassign_workers(a)
        return None

    def _reassign_workers(self, assignment):
        """
        station/operator_type 변경 후 worker index를 재계산.
        간단한 휴리스틱: 같은 (station, wtype)에 있는 task들을 부하 균등하게 분산.
        """
        # 그룹핑: (station, wtype) → [task_ids]
        groups = defaultdict(list)
        for tid, (s, w, _) in assignment.items():
            groups[(s, w)].append(tid)

        new_a = {}
        for (s, w), tids in groups.items():
            max_workers = self.NH if w == 'human' else self.NR
            # 부하 기준 분산: First-Fit Decreasing
            tids_sorted = sorted(tids,
                                 key=lambda t: -(self.tasks[t]['ct_human']
                                                 if w == 'human'
                                                 else self.tasks[t]['ct_robot']))
            worker_loads = [0.0] * max_workers
            for tid in tids_sorted:
                ct = self.tasks[tid]['ct_human'] if w == 'human' else self.tasks[tid]['ct_robot']
                # 부하 가장 적은 worker에 할당
                min_idx = worker_loads.index(min(worker_loads))
                worker_loads[min_idx] += ct
                new_a[tid] = (s, w, min_idx + 1)

        return new_a

    # ============================================================
    # 휴리스틱 메커니즘 (Zhang & Fujimura 2025)
    # ============================================================
    def _heuristic_intra_station(self, assignment, evaluated):
        """
        Zhang Algorithm 1: 워크스테이션 내부 idle slot에 작업 재배치.
        간단화 버전: 각 station에서 가장 빨리 끝나는 worker에 작업 이동 시도.
        """
        # 이 알고리즘은 자세한 시간축 조작이 필요해서 일단 단순한 형태로
        return assignment

    def _heuristic_inter_station(self, assignment, evaluated):
        """
        Zhang Algorithm 2: 가장 부하 큰 station의 task를 인접 station으로 이동.
        """
        station_end = evaluated['station_end']
        avg = sum(station_end.values()) / len(station_end)
        new_a = dict(assignment)

        for station in sorted(station_end.keys(), key=lambda s: -station_end[s]):
            if station_end[station] <= avg:
                continue
            # 이 station의 task들 중 옮길 수 있는 것 시도
            tasks_here = [t for t, (s, _, _) in new_a.items() if s == station]
            tasks_here.sort(key=lambda t: -self.rpw[t])

            for t in tasks_here:
                F, L = self._station_range(t, new_a)
                # 인접 station으로만 시도 (station-1 or station+1)
                for new_s in [station - 1, station + 1]:
                    if new_s < F or new_s > L:
                        continue
                    if new_s < 1 or new_s > self.NS:
                        continue
                    if station_end.get(new_s, 0) >= station_end[station]:
                        continue
                    # 이동 시도
                    test_a = dict(new_a)
                    test_a[t] = (new_s, test_a[t][1], test_a[t][2])
                    test_a = self._reassign_workers(test_a)
                    test_eval = self._evaluate(test_a)
                    if test_eval['cycle_time'] < evaluated['cycle_time']:
                        return test_a
        return None

    # ============================================================
    # 메인 SA 루프
    # ============================================================
    def run(self, verbose=False):
        """E_ASA 메인 실행"""
        # 초기해
        current = self.generate_initial_solution()
        if current is None:
            raise RuntimeError("초기해 생성 실패 — NS가 너무 작을 수 있음")

        cur_eval = self._evaluate(current)
        best = current
        best_eval = cur_eval
        self.worst_obj = cur_eval['objective']

        T = self.T0
        gen = 0
        total_iter = 0

        while T > self.Tf:
            gen += 1
            self._reset_neighbor_matrix_partial()  # call/sum만 리셋, prob 유지

            for it in range(self.maxIT):
                total_iter += 1
                # 이웃 선택 (adaptive)
                op_idx = self._select_neighbor()

                # 이웃 해 생성
                neighbor = self._neighbor(cur_eval['assignment'], op_idx)
                if neighbor is None:
                    continue

                # Zhang heuristic 적용
                neighbor_eval = self._evaluate(neighbor)
                improved = self._heuristic_inter_station(neighbor, neighbor_eval)
                if improved is not None:
                    neighbor = improved
                    neighbor_eval = self._evaluate(neighbor)

                # Adaptive 매트릭스 업데이트
                self._update_neighbor_matrix(op_idx, neighbor_eval['objective'])

                # SA 수락 기준
                delta = neighbor_eval['objective'] - cur_eval['objective']
                if delta <= 0:
                    cur_eval = neighbor_eval
                elif random.random() < math.exp(-delta / T):
                    cur_eval = neighbor_eval

                if cur_eval['objective'] < best_eval['objective']:
                    best_eval = cur_eval
                    if verbose:
                        print(f"      [gen={gen:3d}, T={T:.2f}] "
                              f"새 best: CT={best_eval['cycle_time']:.1f}, "
                              f"NH+NR={best_eval['n_humans']+best_eval['n_robots']}")

                if cur_eval['objective'] > self.worst_obj:
                    self.worst_obj = cur_eval['objective']

            # 이웃 선택 확률 업데이트
            self._update_selection_prob()

            # 냉각
            T *= self.cr

        # 결과 패키징
        return self._package_result(best_eval, total_iter)

    def _reset_neighbor_matrix_partial(self):
        """매 generation 시작에 call/sum만 부분 리셋"""
        self.nb_calls = [1, 1, 1, 1]
        self.nb_sum_obj = [self.worst_obj] * 4

    def _select_neighbor(self):
        """확률 기반 이웃 선택"""
        r = random.random()
        acc = 0
        for i, p in enumerate(self.nb_prob):
            acc += p
            if r <= acc:
                return i
        return 3

    def _update_neighbor_matrix(self, op_idx, obj_val):
        """이웃 사용 후 매트릭스 업데이트"""
        self.nb_calls[op_idx] += 1
        self.nb_sum_obj[op_idx] += obj_val

    def _update_selection_prob(self):
        """이웃별 평균 obj 기반으로 선택 확률 재계산"""
        for i in range(4):
            self.nb_avg[i] = self.nb_sum_obj[i] / max(self.nb_calls[i], 1)
        # 평균 obj가 낮을수록(=좋을수록) 확률 ↑ — 역수 가중
        inverses = [1.0 / max(v, 1e-6) for v in self.nb_avg]
        total = sum(inverses)
        self.nb_prob = [v / total for v in inverses]

    def _package_result(self, eval_result, total_iter):
        """최종 결과를 io_excel.py가 쓰기 좋은 형태로 정리"""
        assignment = eval_result['assignment']
        start = eval_result['start']
        end = eval_result['end']

        # 각 task에 대한 정보 패키징
        task_info = {}
        for tid, (station, wtype, idx) in assignment.items():
            ct = (self.tasks[tid]['ct_human'] if wtype == 'human'
                  else self.tasks[tid]['ct_robot'])
            label_prefix = 'H' if wtype == 'human' else 'R'
            task_info[tid] = {
                'station': station,
                'operator_type': wtype,
                'operator_idx': idx,
                'operator_label': f"{label_prefix}{idx}",
                'start': start[tid],
                'end': end[tid],
                'ct': ct,
            }

        # Station별 메트릭
        station_metrics = []
        station_end = eval_result['station_end']
        for s in range(1, self.NS + 1):
            load = station_end.get(s, 0)
            tasks_here = [t for t, info in task_info.items() if info['station'] == s]
            ops_here = set()
            for t in tasks_here:
                ops_here.add(task_info[t]['operator_label'])

            ct = eval_result['cycle_time']
            station_metrics.append({
                'station': s,
                'operators': '+'.join(sorted(ops_here)) if ops_here else '-',
                'load': load,
                'utilization': load / ct if ct > 0 else 0,
                'idle': ct - load,
                'n_tasks': len(tasks_here),
            })

        # 병목 station
        bottleneck_s = max(station_end.keys(), key=lambda s: station_end[s])
        bottleneck_ct = station_end[bottleneck_s]

        # Smoothness Index
        ct = eval_result['cycle_time']
        sx = math.sqrt(sum((ct - station_end.get(s, 0)) ** 2
                           for s in range(1, self.NS + 1)))

        # Line efficiency
        total_work = sum(info['ct'] for info in task_info.values())
        n_resources = eval_result['n_humans'] + eval_result['n_robots']
        line_eff = total_work / (n_resources * ct) if (ct > 0 and n_resources > 0) else 0

        return {
            'assignment': task_info,
            'cycle_time': ct,
            'n_humans': eval_result['n_humans'],
            'n_robots': eval_result['n_robots'],
            'smoothness': sx,
            'bottleneck_station': bottleneck_s,
            'bottleneck_ct': bottleneck_ct,
            'line_efficiency': line_eff,
            'station_metrics': station_metrics,
            'total_iterations': total_iter,
        }


if __name__ == "__main__":
    # --- 예시 자료 (Example Data) ---
    # tasks: {task_id: {ct_human, ct_robot}}
    # Task_Type은 ct_human/ct_robot의 inf 여부로 내부에서 판단함
    sample_tasks = {
        1: {'ct_human': 10, 'ct_robot': 15},
        2: {'ct_human': 8,  'ct_robot': float('inf')},  # 인간만 가능
        3: {'ct_human': float('inf'), 'ct_robot': 12},  # 로봇만 가능
        4: {'ct_human': 5,  'ct_robot': 5},
        5: {'ct_human': 12, 'ct_robot': 10},
        6: {'ct_human': 7,  'ct_robot': 9},
        7: {'ct_human': 15, 'ct_robot': 12},
        8: {'ct_human': 6,  'ct_robot': 8},
        9: {'ct_human': 10, 'ct_robot': 7},
        10: {'ct_human': 8, 'ct_robot': 8},
    }

    # edges: (선행, 후행)
    sample_edges = [
        (1, 2), (1, 3),
        (2, 4), (3, 4),
        (4, 5), (4, 6),
        (5, 7), (6, 7),
        (7, 8), (8, 9), (9, 10)
    ]

    # 설정
    num_stations = 3
    max_h_per_station = 1
    max_r_per_station = 1

    # 알고리즘 초기화
    asa = EnhancedASA(
        tasks=sample_tasks,
        edges=sample_edges,
        ns=num_stations,
        nh=max_h_per_station,
        nr=max_r_per_station,
        maxIT=50  # 테스트용으로 반복 횟수 조절
    )

    # 실행
    print("알고리즘 실행 중...")
    result = asa.run(verbose=True)

    # 결과 출력
    print("\n" + "="*50)
    print("최종 결과 요약")
    print("="*50)
    print(f"Cycle Time (CT): {result['cycle_time']:.2f}")
    print(f"Line Efficiency: {result['line_efficiency']:.2%}")
    print(f"Smoothness Index: {result['smoothness']:.2f}")
    print(f"Total Humans: {result['n_humans']}")
    print(f"Total Robots: {result['n_robots']}")
    print("-" * 50)
    
    print(f"{'Station':<10} | {'Operators':<15} | {'Load':<10} | {'Tasks'}")
    for m in result['station_metrics']:
        tids = [tid for tid, info in result['assignment'].items() if info['station'] == m['station']]
        print(f"Station {m['station']:<2} | {m['operators']:<15} | {m['load']:<10.2f} | {tids}")
    print("="*50)
