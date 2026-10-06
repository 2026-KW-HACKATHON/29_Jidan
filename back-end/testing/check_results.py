"""Fail the execution gate when a pytest JUnit report omits successful coverage.

`--allow-skipped` is only for the full Python suite: it has deliberate skips (SQLite variants of
MySQL lock tests, opt-in large/real-AI tests, strict xfails of known contract gaps). MySQL skips
cannot hide there because the run sets JIDAN_REQUIRE_MYSQL=1, which aborts instead of skipping.
Failures, errors, empty and malformed reports still fail, and some test must actually run.
"""
import sys
from pathlib import Path
from xml.etree import ElementTree


def validate_report(path: Path, *, allow_skipped: bool = False) -> None:
    root = ElementTree.parse(path).getroot()
    if root.tag == "testsuite":
        suites = [root]
    elif root.tag == "testsuites" and all(node.tag == "testsuite" for node in root):
        suites = list(root)
    else:
        raise ValueError("unexpected report structure")
    if not suites:
        raise ValueError("no test suites")
    total = executed = 0
    for suite in suites:
        if suite.findall("testsuite"):
            raise ValueError("nested test suites are not pytest reports")
        counts = {key: int(suite.attrib[key]) for key in ("tests", "failures", "errors", "skipped")}
        if any(value < 0 for value in counts.values()):
            raise ValueError("negative result count")
        rejected = ("failures", "errors") if allow_skipped else ("failures", "errors", "skipped")
        if any(counts[key] for key in rejected):
            raise ValueError("failed, errored or skipped tests")
        cases = suite.findall("testcase")
        skipped = sum(case.find("skipped") is not None for case in cases)
        if len(cases) != counts["tests"] or skipped != counts["skipped"] or any(
            case.find(tag) is not None for case in cases for tag in ("failure", "error")
        ) or (skipped and not allow_skipped):
            raise ValueError("inconsistent or unsuccessful test cases")
        total += counts["tests"]
        executed += counts["tests"] - skipped
    if total == 0 or executed == 0:
        raise ValueError("no tests executed")


def main(arguments: list[str]) -> int:
    allow_skipped = arguments[:1] == ["--allow-skipped"]
    arguments = arguments[1:] if allow_skipped else arguments
    if not arguments:
        print("At least one JUnit report is required", file=sys.stderr)
        return 2
    for argument in arguments:
        try:
            validate_report(Path(argument), allow_skipped=allow_skipped)
        except (OSError, ElementTree.ParseError, KeyError, ValueError) as error:
            print(f"Invalid test report {argument}: {error}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
