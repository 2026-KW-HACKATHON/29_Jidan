"""Markdown evaluation report of one manual-interview run (`e2e.interview_eval`).

`render(record)` is pure: `record` holds what the runner saw through the public API (questions,
the owner's scripted answers, reviews, the draft) and the server's AI call trace
(`e2e.ai_scenario.CallTrace`: judgements, latencies, grounding counts, token usage). Nothing here
reads the environment, so no key can reach the report; `config` is a whitelist the runner fills.
"""
import statistics
from collections import defaultdict

from e2e import owner_persona

WRITING_OPS = ("summarize_intent", "revise_structure", "compose_draft")
OP_ORDER = ("judge_sufficiency", "generate_question", "summarize_intent", "revise_structure", "compose_draft",
            "answer_question", "transcribe")
# OpenAPI ManualMissingInformation / issue fields shown in the report.
MISSING_FIELDS = ("target", "field", "description")


def cell(value) -> str:
    """One Markdown table cell: no pipes or line breaks."""
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def _ms(values: list[int]) -> str:
    if not values:
        return "-"
    return (f"{len(values)}회 · 평균 {statistics.fmean(values) / 1000:.1f}s · 중앙 {statistics.median(values) / 1000:.1f}s"
            f" · 최대 {max(values) / 1000:.1f}s")


def judgement_text(entry: dict | None) -> str:
    if entry is None:
        return "-"
    if entry.get("outcome") != "ok":
        return f"실패({entry.get('outcome')})"
    verdict = "충분" if entry.get("sufficient") else "불충분"
    text = f"{verdict} p={entry.get('probability', 0):.2f}"
    if entry.get("missing_aspects"):
        text += " · 부족: " + ", ".join(entry["missing_aspects"])
    return text


def _calls_for(trace: list[dict], op: str, intent: str, depth: int, kind: str | None = None) -> list[dict]:
    return [e for e in trace if e.get("op") == op and e.get("intent") == intent and e.get("depth") == depth
            and (kind is None or e.get("kind") == kind)]


def _last_ok(entries: list[dict]) -> dict | None:
    ok = [e for e in entries if e.get("outcome") == "ok"]
    return ok[-1] if ok else (entries[-1] if entries else None)


def _attempts(entries: list[dict]) -> str:
    return f" ({len(entries)}회 시도)" if len(entries) > 1 else ""


def _steps_lines(sections: list[dict], shift_names: dict[str, str]) -> list[str]:
    lines = []
    for section in sections:
        where = shift_names.get(section.get("shiftId") or "", "")
        head = f"- **{section.get('title')}** ({section.get('category')}{' · ' + where if where else ''})"
        steps = section.get("steps") or []
        lines.append(head + ("" if steps else " — 단계 없음"))
        for number, step in enumerate(steps, 1):
            check = " [체크]" if step.get("checklistItem") else ""
            lines.append(f"  {number}. {step.get('instruction')}{check}")
    return lines


def _shift_rows(shifts: list[dict]) -> list[str]:
    if not shifts:
        return ["(근무조 없음)"]
    rows = ["| 근무조 | 시작 | 종료 | 익일 종료 |", "| --- | --- | --- | --- |"]
    for shift in shifts:
        rows.append(f"| {cell(shift.get('name'))} | {cell(shift.get('startTime') or '미확정')} | "
                    f"{cell(shift.get('endTime') or '미확정')} | {cell(shift.get('endsNextDay'))} |")
    return rows


def _missing_lines(items: list[dict]) -> list[str]:
    return [f"- {' · '.join(cell(item.get(name)) for name in MISSING_FIELDS if item.get(name))}" for item in items]


def _structure(content: dict) -> list[str]:
    shift_names = {s.get("id"): s.get("name") for s in content.get("shifts") or []}
    lines = _shift_rows(content.get("shifts") or []) + [""]
    lines += _steps_lines(content.get("sections") or [], shift_names) or ["(섹션 없음)"]
    missing = content.get("missingInformation") or []
    if missing:
        lines += ["", f"미확정 정보 {len(missing)}건:"] + _missing_lines(missing)
    return lines


def observations(record: dict) -> list[str]:
    """Automatic flags worth a human look (not failures)."""
    notes: list[str] = []
    trace, turns = record.get("trace") or [], record.get("turns") or []
    skip = record.get("skip_depth_five", False)
    by_intent: dict[str, list[dict]] = defaultdict(list)
    for turn in turns:
        by_intent[turn["intent"]].append(turn)
    for key, items in by_intent.items():
        reached = max(t["depth"] for t in items)
        expected = owner_persona.expected_depth(key, skip_depth_five=skip) if key in owner_persona.ANSWERS else None
        if expected is not None and reached != expected:
            notes.append(f"{key}: 도달 깊이 {reached} ≠ 페르소나 기대 {expected} "
                         f"({'더 캐물음' if reached > expected else '덜 캐물음'})")
        texts = [t["question"] for t in items]
        if len(set(texts)) < len(texts):
            notes.append(f"{key}: 같은 질문 문장이 반복됨")
        for turn in items:
            judged = _last_ok(_calls_for(trace, "judge_sufficiency", key, turn["depth"]))
            if judged is None or judged.get("outcome") != "ok":
                continue
            covers = owner_persona.covered(key, turn.get("answer", "")) if key in owner_persona.ANSWERS else None
            if covers is False and judged.get("sufficient"):
                notes.append(f"{key} depth {turn['depth']}: 모호하게 쓴 답을 충분으로 판단(p={judged['probability']:.2f})")
            if covers is True and not judged.get("sufficient"):
                notes.append(f"{key} depth {turn['depth']}: 구체적으로 쓴 답을 불충분으로 판단 "
                             f"(부족: {', '.join(judged.get('missing_aspects') or [])})")
    dropped = sum(e.get("dropped_steps", 0) for e in trace)
    cleared = sum(e.get("cleared_shifts", 0) for e in trace)
    if dropped or cleared:
        notes.append(f"근거 없는 내용 제거: 단계 {dropped}개, 근무조 시간 {cleared}개 (아래 근거 통계)")
    failed = [e for e in trace if e.get("outcome") != "ok"]
    if failed:
        kinds = defaultdict(int)
        for entry in failed:
            kinds[f"{entry['op']}:{entry.get('outcome')}"] += 1
        notes.append("실패한 AI 호출: " + ", ".join(f"{k} x{v}" for k, v in sorted(kinds.items())))
    for key, review in (record.get("reviews") or {}).items():
        if review.get("status") != "READY":
            notes.append(f"{key} 검토: {review.get('status')} {review.get('error') or ''}".rstrip())
            continue
        content = review.get("content") or {}
        if not (content.get("shifts") or content.get("sections")):
            notes.append(f"{key} 검토: 근무조·섹션이 비어 있음")
        if any(not s.get("steps") for s in content.get("sections") or []):
            notes.append(f"{key} 검토: 단계가 빈 섹션이 있음")
    draft = record.get("draft") or {}
    content = draft.get("content") or {}
    if draft and any(not s.get("steps") for s in content.get("sections") or []):
        notes.append("초안: 단계가 빈 섹션이 있음")
    return notes


def render(record: dict) -> str:
    trace = record.get("trace") or []
    live = [e for e in trace if e.get("live")]
    results = record.get("results") or []
    passed = sum(1 for status, *_ in results if status == "PASS")
    ok = bool(results) and passed == len(results)
    calls = record.get("calls") or {}
    lines = [
        f"# 매뉴얼 인터뷰 평가 리포트 ({record.get('run')})",
        "",
        "| 항목 | 값 |",
        "| --- | --- |",
        f"| 결과 | {'PASS' if ok else 'FAIL'} ({passed}/{len(results)} 단계) |",
        f"| 시작 | {cell(record.get('started_at'))} |",
        f"| AI 모드 | {cell(record.get('mode'))} · 실호출 연산 {cell(', '.join(record.get('live_ops') or []) or '없음')} |",
        f"| 페르소나 | {cell(owner_persona.STORE)} · depth 5 경로 {'생략' if record.get('skip_depth_five') else '포함'} |",
        (f"| 실호출 수 | {len(live)} / 한도 {cell(calls.get('limit', record.get('call_limit')))}"
        f" (거부 {calls.get('refused', 0)}) |"),
        f"| 전체 소요 | {record.get('wall_seconds', 0):.0f}s |",
    ]
    config = record.get("config") or {}
    if config:
        lines += ["", "## 설정", ""] + [f"- `{name}` = `{value}`" for name, value in config.items()]
        versions = sorted({e["config"] for e in live if e.get("config")})
        if versions:
            lines += ["- 실제 호출 config_version:"] + [f"  - `{v}`" for v in versions]

    lines += ["", "## 단계 결과", ""]
    lines += [f"- [{status}] {label}" + (f" — {cell(detail)}" if detail else "") for status, label, detail in results]

    notes = observations(record)
    lines += ["", "## 자동 관찰 포인트", ""] + ([f"- {n}" for n in notes] or ["- 없음"])

    lines += ["", "## 인텐트별 질문·답변·판단", ""]
    turns = record.get("turns") or []
    reviews = record.get("reviews") or {}
    for intent in record.get("intents") or []:
        key = intent["key"]
        items = [t for t in turns if t["intent"] == key]
        lines += [f"### {key}", ""]
        if not items:
            lines += ["(질문 없음)", ""]
            continue
        lines += ["| 깊이 | 종류 | 질문 | 점주 답변 | Jev 판단 | 질문 생성 | 판단 |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        for turn in items:
            asked = _calls_for(trace, "generate_question", key, turn["depth"], turn["kind"])
            judged = _calls_for(trace, "judge_sufficiency", key, turn["depth"])
            question = turn["question"]
            if turn.get("guidance"):
                question += f"<br>(안내: {turn['guidance']})"
            asked_ms = _last_ok(asked)
            judged_ms = _last_ok(judged)
            lines.append(
                f"| {turn['depth']} | {turn['kind']} | {cell(question)} | {cell(turn.get('answer', '-'))} | "
                f"{cell(judgement_text(_last_ok(judged)))}{_attempts(judged)} | "
                f"{(asked_ms or {}).get('ms', '-')}ms{_attempts(asked)} | {(judged_ms or {}).get('ms', '-')}ms |")
        reached = max(t["depth"] for t in items)
        review = reviews.get(key) or {}
        needs = (review.get("content") or {}).get("needsDetail")
        final = _last_ok(_calls_for(trace, "judge_sufficiency", key, reached))
        outcome = ("NEEDS_DETAIL" if needs else "충분" if final and final.get("sufficient") else
                   "판단 없음" if final is None else "불충분")
        expected = (owner_persona.expected_depth(key, skip_depth_five=record.get("skip_depth_five", False))
                    if key in owner_persona.ANSWERS else "-")
        lines += ["", f"도달 깊이 **{reached}** (페르소나 기대 {expected}) · 결과 **{outcome}**", ""]

    lines += ["## 검토 요약", ""]
    for key, review in reviews.items():
        content = review.get("content") or {}
        lines += [f"### {key} — {review.get('status')}" + (" · needsDetail" if content.get("needsDetail") else ""), ""]
        if review.get("error"):
            lines += [f"오류: {cell(review['error'])}", ""]
        if content:
            lines += [f"> {cell(content.get('summary'))}", ""] + _structure(content) + [""]

    correction = record.get("correction")
    if correction:
        lines += ["## 검토 정정 (revise_structure)", "",
                  f"- 대상: {correction.get('intent')} · 지시: {cell(correction.get('instruction'))}",
                  f"- 결과: {correction.get('status')}", "", "정정 전:", ""]
        lines += _shift_rows(correction.get("before") or []) + ["", "정정 후:", ""]
        lines += _shift_rows(correction.get("after") or []) + [""]

    draft = record.get("draft")
    lines += ["## 최종 초안", ""]
    if not draft:
        lines += ["(초안 없음)", ""]
    else:
        content = draft.get("content") or {}
        lines += [(f"generationStatus {draft.get('generationStatus')} · revision {draft.get('revision')} · "
                  f"근무조 {len(content.get('shifts') or [])} · 섹션 {len(content.get('sections') or [])} · "
                  f"단계 {sum(len(s.get('steps') or []) for s in content.get('sections') or [])}"), ""]
        lines += _structure(content) + [""]
        issues = draft.get("issues") or []
        if issues:
            lines += [f"검토 이슈 {len(issues)}건:"]
            lines += [f"- [{i.get('status')}] {cell(i.get('description'))}" for i in issues]
            lines += [""]

    lines += ["## 근거(그라운딩) 통계", "",
              "| 연산 | 호출 | 근거 조각(평균) | 제거된 단계 | 비운 근무조 시간 |", "| --- | --- | --- | --- | --- |"]
    for op in WRITING_OPS:
        entries = [e for e in trace if e.get("op") == op]
        if not entries:
            continue
        evidence = [e["evidence"] for e in entries if "evidence" in e]
        lines.append(f"| {op} | {len(entries)} | {statistics.fmean(evidence):.1f} |" if evidence else
                     f"| {op} | {len(entries)} | - |")
        lines[-1] += (f" {sum(e.get('dropped_steps', 0) for e in entries)} |"
                      f" {sum(e.get('cleared_shifts', 0) for e in entries)} |")
    lines += ["", "제거 수는 서버 로그(`ai grounding`)의 개수만 집계한다. 제공자 원문은 남기지 않는다.", ""]

    lines += ["## 지연 시간·호출 수·토큰", "",
              "| 연산 | 실호출 | fake | 실패 | 지연(실호출) | 입력 토큰 | 출력 토큰 | 추론 토큰 |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    totals = defaultdict(int)
    for op in OP_ORDER:
        entries = [e for e in trace if e.get("op") == op]
        if not entries:
            continue
        live_entries = [e for e in entries if e.get("live")]
        usage = defaultdict(int)
        for entry in live_entries:
            for name, value in (entry.get("usage") or {}).items():
                usage[name] += value
                totals[name] += value
        lines.append(
            f"| {op} | {len(live_entries)} | {len(entries) - len(live_entries)} | "
            f"{sum(1 for e in entries if e.get('outcome') != 'ok')} | {_ms([e['ms'] for e in live_entries])} | "
            f"{usage.get('input_tokens', '-')} | {usage.get('output_tokens', '-')} | "
            f"{usage.get('output_tokens_details.reasoning_tokens', '-')} |")
    lines += ["", f"실호출 합계 {len(live)}회 · AI 대기 합계 {sum(e['ms'] for e in live) / 1000:.0f}s"]
    if totals:
        lines.append("토큰 합계: " + ", ".join(f"{name} {value}" for name, value in sorted(totals.items())))
        price = record.get("price_per_1m")
        if price:
            cost = (totals.get("input_tokens", 0) * price[0] + totals.get("output_tokens", 0) * price[1]) / 1_000_000
            lines.append(f"추정 비용: ${cost:.4f} (입력 ${price[0]}/1M, 출력 ${price[1]}/1M 가정)")
    lines.append("")
    return "\n".join(lines)
