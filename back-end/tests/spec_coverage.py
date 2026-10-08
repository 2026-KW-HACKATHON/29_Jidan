"""OpenAPI coverage of the test suite: which operations and documented statuses tests exercise.

Collect (only when asked; the normal test run is unchanged):

    JIDAN_SPEC_COVERAGE=/tmp/spec-coverage.json python -m pytest

Every response a real application handler returns to any `TestClient` in the run is recorded as
(method, OpenAPI path template, status) -- from `app.main.app` or from a test app that mounts the
real routers (same FastAPI operationId). Test-only stub handlers are ignored. Paths under `/api/health` and `/api/swagger` are deployment checks, not the contract.

Report:

    python -m tests.spec_coverage report /tmp/spec-coverage.json

lists, per area (by OpenAPI tag):
  (a) operations in the spec that the application does not route,
  (b) routed operations no test ever called,
  (c) documented statuses no test ever produced (429 and 500 are reported apart: every operation
      documents them, but only a few paths can actually produce them),
and application routes that the spec does not describe.
"""
import json
import os
import re
import sys
import weakref
from collections import defaultdict
from pathlib import Path

ENV = "JIDAN_SPEC_COVERAGE"
METHODS = ("get", "post", "put", "patch", "delete")
COMMON_STATUSES = {"429", "500"}
UNCHECKED_PREFIXES = ("/api/health", "/api/swagger")

AREAS = {
    "store": ("근무자 초대", "점주 매장 관리", "근무자 관리", "일반회원 초대함", "매장 승인 관리", "근무자 매장 선택"),
    "jobs": ("대타 근무 요청", "점주 대타 공고", "일반회원 공고 지원", "대타 공고 탐색", "점주 지원자 확인",
             "근무 캘린더", "홈 요약"),
    "notify": ("공통 알림", "관심 매장"),
    "profile/auth": ("일반회원 프로필", "가입", "세션", "Google 인증"),
    "manual·AI": ("점주 AI 인터뷰", "점주 매뉴얼", "AI 질문 미디어", "근무자 AI 업무 질문", "매뉴얼 미디어",
                  "근무자 매뉴얼"),
}
AREA_ORDER = (*AREAS, "other")


def area_of(operation: dict) -> str:
    tags = operation.get("tags") or []
    for area, area_tags in AREAS.items():
        if any(tag in area_tags for tag in tags):
            return area
    return "other"


def spec_operations() -> dict[tuple[str, str], dict]:
    from tests.auth_contract import SPEC

    return {
        (method.upper(), template): operation
        for template, item in SPEC["paths"].items()
        for method, operation in item.items() if method in METHODS
    }


def _shape(template: str) -> str:
    """A template with parameter names erased: `/a/{x}/b` and `/a/{y}/b` are the same route."""
    return re.sub(r"\{[^}/]+\}", "{}", template)


def app_routes() -> set[tuple[str, str]]:
    """(METHOD, path) of every /api route the application serves.

    Read from FastAPI's own generated schema: it flattens included routers, which recent FastAPI
    keeps nested (`_IncludedRouter`) in `app.routes`.
    """
    from app.main import app

    return {
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        if path.startswith("/api/") and not path.startswith(UNCHECKED_PREFIXES)
        for method in item if method in METHODS
    }


# --- collection -----------------------------------------------------------------------------------


class Collector:
    """pytest plugin: wraps TestClient.request for the session and writes the hits at the end."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.hits: set[tuple[str, str, str]] = set()
        self.unmatched: set[tuple[str, str, str]] = set()
        self._original = None

    def pytest_sessionstart(self, session) -> None:
        from fastapi.testclient import TestClient

        collector = self
        original = TestClient.request
        self._original = original

        def request(client, method, url, *args, **kwargs):
            response = original(client, method, url, *args, **kwargs)
            collector.record(client, response)
            return response

        TestClient.request = request

    def record(self, client, response) -> None:
        from tests.api_contract import find_operation

        path = response.request.url.path
        if not path.startswith("/api/") or path.startswith(UNCHECKED_PREFIXES):
            return
        method = response.request.method
        found = find_operation(path, method)
        if found is None:
            if client.app is _main_app():
                self.unmatched.add((method, path, str(response.status_code)))
            return
        if _serves_real_handler(client.app, method, found[0]):
            self.hits.add((method, found[0], str(response.status_code)))

    def pytest_sessionfinish(self, session, exitstatus) -> None:
        from fastapi.testclient import TestClient

        if self._original is not None:
            TestClient.request = self._original
        self.path.write_text(json.dumps({
            "hits": sorted(self.hits),
            "unmatched": sorted(self.unmatched),
        }, ensure_ascii=False, indent=0))


def _main_app():
    from app.main import app

    return app


# Keyed by the app object itself (not id(): short-lived test apps reuse addresses).
_OPERATION_IDS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _operation_ids(app) -> dict[tuple[str, str], str]:
    """(METHOD, path shape) -> FastAPI operationId (handler name + path) of an application."""
    key = app
    if key not in _OPERATION_IDS:
        try:
            paths = app.openapi()["paths"]
        except Exception:  # noqa: BLE001 - a non-FastAPI test app simply has no real handlers
            paths = {}
        _OPERATION_IDS[key] = {
            (method.upper(), _shape(path)): operation.get("operationId", "")
            for path, item in paths.items() for method, operation in item.items() if method in METHODS
        }
    return _OPERATION_IDS[key]


def _serves_real_handler(app, method: str, template: str) -> bool:
    """True when `app` answers this operation with the real application's handler.

    Tests often mount the real routers on a fresh FastAPI app; those count. A test-only stub at
    a spec path (another function) does not: its operationId differs from the real one.
    """
    if app is _main_app():
        return True
    real = _operation_ids(_main_app()).get((method, _shape(template)))
    return real is not None and _operation_ids(app).get((method, _shape(template))) == real


def plugin_from_env() -> Collector | None:
    path = os.getenv(ENV, "").strip()
    return Collector(path) if path else None


# --- report ---------------------------------------------------------------------------------------


def analyze(data: dict) -> dict:
    operations = spec_operations()
    routes = app_routes()
    route_shapes = {(method, _shape(path)) for method, path in routes}
    spec_shapes = {(method, _shape(template)) for method, template in operations}
    seen: dict[tuple[str, str], set[str]] = defaultdict(set)
    for method, template, status in data.get("hits", []):
        seen[(method, template)].add(status)

    result = {area: {"unimplemented": [], "uncalled": [], "missing": [], "missing_common": [],
                     "operations": 0, "implemented": 0, "called": 0, "statuses": 0, "covered": 0}
              for area in AREA_ORDER}
    for (method, template), operation in sorted(operations.items(), key=lambda item: (item[0][1], item[0][0])):
        bucket = result[area_of(operation)]
        bucket["operations"] += 1
        documented = set(operation["responses"])
        label = f"{method} {template}"
        if (method, _shape(template)) not in route_shapes:
            bucket["unimplemented"].append(label)
            continue
        bucket["implemented"] += 1
        hit = seen.get((method, template), set())
        if not hit:
            bucket["uncalled"].append(label)
            continue
        bucket["called"] += 1
        specific = documented - COMMON_STATUSES
        bucket["statuses"] += len(specific)
        bucket["covered"] += len(specific & hit)
        if specific - hit:
            bucket["missing"].append((label, sorted(specific - hit)))
        if COMMON_STATUSES & documented - hit:
            bucket["missing_common"].append((label, sorted(COMMON_STATUSES & documented - hit)))
    undocumented = sorted(
        f"{method} {path}" for method, path in routes if (method, _shape(path)) not in spec_shapes
    )
    return {"areas": result, "undocumented_routes": undocumented, "unmatched": data.get("unmatched", [])}


def render(analysis: dict) -> str:
    lines = []
    for area in AREA_ORDER:
        bucket = analysis["areas"][area]
        if not bucket["operations"]:
            continue
        lines += [
            f"## {area}", "",
            f"- operation {bucket['operations']}개 중 구현 {bucket['implemented']}, 테스트 호출 {bucket['called']}",
            f"- 호출된 operation의 문서화 응답 코드(429·500 제외) {bucket['statuses']}개 중 발생 {bucket['covered']}",
            "",
        ]
        if bucket["unimplemented"]:
            lines += ["### (a) 미구현 operation", "", *[f"- `{x}`" for x in bucket["unimplemented"]], ""]
        if bucket["uncalled"]:
            lines += ["### (b) 구현됐지만 테스트가 부르지 않은 operation", "",
                      *[f"- `{x}`" for x in bucket["uncalled"]], ""]
        if bucket["missing"]:
            lines += ["### (c) 테스트에서 발생하지 않은 문서화 응답 코드", "",
                      *[f"- `{label}`: {', '.join(codes)}" for label, codes in bucket["missing"]], ""]
        if bucket["missing_common"]:
            counts = defaultdict(int)
            for _label, codes in bucket["missing_common"]:
                for code in codes:
                    counts[code] += 1
            summary = ", ".join(f"{code} {count}개 operation" for code, count in sorted(counts.items()))
            lines += [f"- 공통 응답 미발생(참고): {summary}", ""]
    lines += ["## 명세에 없는 앱 라우트", ""]
    lines += [f"- `{x}`" for x in analysis["undocumented_routes"]] or ["- 없음"]
    if analysis["unmatched"]:
        lines += ["", "## 테스트가 부른 명세 밖 경로", "",
                  *[f"- `{m} {p}` → {s}" for m, p, s in analysis["unmatched"]]]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] != "report":
        print("usage: python -m tests.spec_coverage report <coverage.json>", file=sys.stderr)
        return 2
    analysis = analyze(json.loads(Path(argv[1]).read_text()))
    sys.stdout.write(render(analysis))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
