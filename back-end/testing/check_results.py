"""Fail the execution gate when a pytest JUnit report omits successful coverage.

`--allow-skipped` permits only SQLite cases whose matching MySQL case passed in the same
report. Unexpected skips, xfails, MySQL skips, failures, empty and malformed evidence fail.
The full CI suite enables large-content checks and excludes paid OpenAI tests explicitly.
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
    passed_cases = {
        (case.get("classname", ""), case.get("name", ""))
        for case in root.iter("testcase")
        if not any(case.find(tag) is not None for tag in ("failure", "error", "skipped"))
    }
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
        if allow_skipped:
            for case in cases:
                skip = case.find("skipped")
                if skip is None:
                    continue
                name = case.get("name", "")
                counterpart = (case.get("classname", ""), name.replace("[sqlite", "[mysql", 1))
                if ("[sqlite" not in name or skip.get("type") == "pytest.xfail"
                        or counterpart not in passed_cases):
                    raise ValueError("skip has no successful MySQL counterpart")
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
