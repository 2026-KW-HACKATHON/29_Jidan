"""JUnit gating must reject skip/xfail, empty and corrupt coverage evidence."""
# Ported from the remote PR #154 harness; --allow-skipped is the local full-suite adaptation.
import pytest

from testing.check_results import main


def report(*, tests=1, failures=0, errors=0, skipped=0, case="<testcase name='ok'/>"):
    return (f"<testsuites><testsuite tests='{tests}' failures='{failures}' "
            f"errors='{errors}' skipped='{skipped}'>{case}</testsuite></testsuites>")


def test_successful_reports_and_multiple_suites(tmp_path):
    first = tmp_path / "with spaces.xml"
    second = tmp_path / "second.xml"
    first.write_text(report())
    second.write_text(report().replace("</testsuite>", "</testsuite><testsuite tests='0' "
                                      "failures='0' errors='0' skipped='0'/>"))
    assert main([str(first), str(second)]) == 0


@pytest.mark.parametrize("xml", [
    report(skipped=1, case="<testcase><skipped/></testcase>"),
    report(skipped=1, case="<testcase><skipped type='pytest.xfail'/></testcase>"),
    report(failures=1, case="<testcase><failure/></testcase>"),
    report(errors=1, case="<testcase><error/></testcase>"),
    report(tests=0, case=""), report(tests=-1, case=""), report(tests=2),
    report(case="<testcase><skipped/></testcase>"),
    "<testsuites/>", "<testsuite tests='1'/>", "<broken",
    "<unknown><testsuite tests='1' failures='0' errors='0' skipped='0'><testcase/></testsuite></unknown>",
    "<testsuites><testcase/></testsuites>",
    ("<testsuites><testsuite tests='1' failures='0' errors='0' skipped='1'>"
     "<testcase><skipped/></testcase><testsuite tests='1' failures='0' errors='0' skipped='0'>"
     "<testcase/></testsuite></testsuite></testsuites>"),
])
def test_incomplete_reports_fail(tmp_path, xml):
    path = tmp_path / "result.xml"
    path.write_text(xml)
    assert main([str(path)]) == 1


def test_missing_report_arguments_and_files_fail(tmp_path):
    assert main([]) == 2
    assert main([str(tmp_path / "missing.xml")]) == 1
    valid = tmp_path / "valid.xml"
    valid.write_text(report())
    assert main([str(valid), str(tmp_path / "missing.xml")]) == 1


def test_allow_skipped_accepts_deliberate_skips_only(tmp_path):
    path = tmp_path / "python.xml"
    path.write_text(report(tests=2, skipped=1, case="<testcase name='case[mysql]'/><testcase name='case[sqlite]'><skipped/></testcase>"))
    assert main([str(path)]) == 1
    assert main(["--allow-skipped", str(path)]) == 0


@pytest.mark.parametrize("xml", [
    report(failures=1, case="<testcase><failure/></testcase>"),
    report(errors=1, case="<testcase><error/></testcase>"),
    report(tests=1, skipped=1, case="<testcase><skipped/></testcase>"),  # nothing actually ran
    report(tests=2, skipped=0, case="<testcase/><testcase><skipped/></testcase>"),  # count mismatch
    report(tests=0, case=""), "<broken",
])
def test_allow_skipped_still_rejects_failures_and_empty_runs(tmp_path, xml):
    path = tmp_path / "python.xml"
    path.write_text(xml)
    assert main(["--allow-skipped", str(path)]) == 1


def test_allow_skipped_needs_a_report(tmp_path):
    assert main(["--allow-skipped"]) == 2


@pytest.mark.parametrize("case", [
    "<testcase name='case[mysql]'/><testcase name='other[sqlite]'><skipped/></testcase>",
    "<testcase name='other[mysql]'/><testcase name='case[mysql]'><skipped/></testcase>",
    "<testcase name='case[mysql]'/><testcase name='case[sqlite]'><skipped type='pytest.xfail'/></testcase>",
    "<testcase name='case[mysql]' classname='A'/><testcase name='case[sqlite]' classname='B'><skipped/></testcase>",
])
def test_full_suite_skip_exception_requires_a_passed_mysql_counterpart(tmp_path, case):
    path = tmp_path / "python.xml"
    path.write_text(report(tests=2, skipped=1, case=case))
    assert main(["--allow-skipped", str(path)]) == 1
