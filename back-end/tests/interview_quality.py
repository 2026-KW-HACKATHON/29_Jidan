"""Conservative checks for Q-INT-1: one probe question asks one sub-item, one Jev missing aspect
names one aspect of one task. Used by the Fake state-machine tests and the opt-in live test.

They are heuristics, pinned by tests/test_interview_atomic_aspects.py on right and wrong samples
taken from real model output and the reviewer's labels of 23 probes and 20 aspects. They flag only
clear compounds: two different question words ("어떤 … 언제"), two aspect kinds ("순서 … 완료 기준"),
"각각", two tasks named together, or a second yes/no clause joined by -며. A context clause that
recalls the owner's earlier answer ("…하셨는데,") is not part of the question, and idioms that only
look like a kind ("어떻게 되나요", "…부터 … 완료까지") are not counted.
"""

import re

# Aspect kinds as Jev is told to name them (prompts.py judge_sufficiency), plus "종류" (which tasks
# there are), which Jev names on its own. Patterns are regexes.
ASPECT_KINDS = {
    "종류": (r"종류(?!별)",),  # "종류별로 쌓아요" describes a step, not the kind-of-task aspect
    "순서": (r"순서",),
    "방법": (r"방법", r"어떻게", r"방식"),
    "완료 기준": (r"완료", r"끝났다고", r"끝난 것으로", r"기준"),  # "몇 시에 끝나나요" is a time
    "조건": (r"조건", r"경우에"),
    "예외": (r"예외",),
    "시간": (r"시각", r"몇 시", r"시간"),
}
QUESTION_WORDS = ("어떤", "어떻게", "언제", "무엇", "무슨", "어디", "누가", "누구", "왜", "얼마나", "몇")
# Everything up to the last of these is context that recalls the earlier answer.
_CONTEXT_END = re.compile(r"(?:는데요?|셨고|셨죠|이네요|군요)[,.]?\s+|[.!]\s+")
# Wording that looks like an aspect kind but is not one (removed before counting kinds):
# "…은 어떻게 되나요?" is the idiom "what is …", not "how" (the method), and "A부터 B 완료까지"
# names the end of a range of steps, not a completion criterion.
_NOT_A_KIND = (re.compile(r"어떻게\s*(?:되나요|돼요|되세요|되는지|되죠|되어요)"),
               re.compile(r"(부터\s*(?:\S+\s+)*?)\S*\s*완료까지"))
# Two tasks named together: "설거지와 테이블 닦기의 …", "…를 언제". Only 와/랑/하고 (과 ends nouns: 결과).
_TWO_TASKS = re.compile(r"[가-힣](와|랑|하고) [가-힣]")


def question_clause(text: str) -> str:
    """The asking part of a question (the context clause before it removed)."""
    parts = _CONTEXT_END.split(text)
    return parts[-1] if parts else text


def _without_false_kinds(text: str) -> str:
    text = _NOT_A_KIND[0].sub("되나요", text)
    return _NOT_A_KIND[1].sub(r"\1", text)


def kinds_in(text: str) -> set[str]:
    text = _without_false_kinds(text)
    return {kind for kind, patterns in ASPECT_KINDS.items() if any(re.search(p, text) for p in patterns)}


def question_words_in(text: str) -> set[str]:
    found = set()
    for word in QUESTION_WORDS:
        # "어떤"/"어떻게" are distinct words; "몇 시부터 몇 시까지" counts once (one time range).
        if re.search(rf"(?<![가-힣]){word}", text):
            found.add(word)
    return found


def _second_question_after_and(clause: str) -> bool:
    """"각 조는 몇 시에 시작하고 끝나며 마감조는 … 근무하나요?": a question-word clause joined by
    -며 to a second clause with its own topic ("마감조는") asks a second, yes/no sub-item."""
    head, sep, tail = clause.partition("며 ")
    return bool(sep) and bool(question_words_in(head)) and not question_words_in(tail) and bool(
        re.match(r"\S+(은|는) ", tail))


def is_compound_question(text: str) -> bool:
    clause = question_clause(text)
    if "각각" in clause or _second_question_after_and(clause):
        return True
    if len(question_words_in(clause) - {"몇"}) >= 2:
        return True
    kinds = kinds_in(clause)
    # "어떻게" also names the method kind; a single "어떻게 끝났다고" is still one sub-item.
    if kinds == {"방법", "완료 기준"} and "방법" not in clause and "방식" not in clause:
        return False
    return len(kinds) >= 2


def is_compound_aspect(text: str) -> bool:
    if re.search(r"[,，]|\s및\s|그리고|각각", text):
        return True
    kinds = kinds_in(text) - {"시간"}
    # The handling method of an exception is one aspect: "예외 상황이 생겼을 때의 처리 방법".
    if kinds == {"예외", "방법"}:
        return False
    subject = re.split(r"의 |[을를] ", text, maxsplit=1)[0] if re.search(r"의 |[을를] ", text) else ""
    # Two tasks: compound, except their relative order, which is one aspect that cannot be split
    # per task ("기계 닦기와 쓰레기 정리의 작업 순서", boundary case labelled atomic).
    if _TWO_TASKS.search(subject) and kinds != {"순서"}:
        return True
    return len(kinds) >= 2
