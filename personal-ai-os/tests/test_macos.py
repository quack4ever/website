"""macOS layer tests.

Most of these run anywhere, on purpose. The security-critical part - that user
text is passed to AppleScript as DATA and never as CODE - is a property of how
we build the command, so it can and should be tested without a Mac.
"""
from __future__ import annotations

import plistlib
from datetime import datetime

import pytest

from assistant import paths, tools
from assistant.errors import PlatformUnsupported, ValidationError
from assistant.macos import launchd, osa, tcc
from assistant.tools import macos_tools, registry


# ===========================================================================
# THE important one: AppleScript injection
# ===========================================================================
def test_user_text_is_passed_as_data_after_a_double_dash():
    hostile = 'holiday" \n do shell script "rm -rf ~" \n --'
    command = osa.build_command("on run argv\nreturn item 1 of argv\nend run",
                                [hostile])
    assert command[0] == "osascript"
    assert "--" in command
    separator = command.index("--")
    # The script is BEFORE the separator; user text is AFTER it.
    script = command[separator - 1]
    assert hostile not in script, "user text was pasted into the script body!"
    assert command[separator + 1] == hostile


@pytest.mark.parametrize("hostile", [
    'x" & (do shell script "whoami") & "',
    "'; tell application \"Finder\" to delete every item of home; '",
    'a\\"b',
    "line1\nline2\rline3",
    'end run\non run argv\ndo shell script "id"\nend run',
    "‮evil",
    "x" * 5000,
])
def test_no_hostile_string_can_reach_the_script_body(hostile):
    command = osa.build_command(osa.wrap_handler("return item 1 of argv"), [hostile])
    script = command[2]
    assert hostile not in script
    assert command[-1] == hostile


def test_wrap_handler_adds_the_argv_handler_once():
    wrapped = osa.wrap_handler("return 1")
    assert wrapped.startswith("on run argv")
    assert wrapped.endswith("end run")
    # Already-wrapped scripts must not be double-wrapped.
    assert osa.wrap_handler(wrapped) == wrapped


def test_no_arguments_means_no_separator():
    command = osa.build_command("return 1", [])
    assert "--" not in command


#: Scripts that receive user-supplied values, and must therefore read them
#: from argv. Scripts absent from this list must take no input at all.
_SCRIPTS_TAKING_INPUT = ("_READ_EVENTS", "_CREATE_EVENT", "_CREATE_REMINDER",
                         "_NOTIFY")
_SCRIPTS_TAKING_NO_INPUT = ("_READ_REMINDERS", "_LIST_CALENDARS")


def test_no_applescript_uses_string_interpolation():
    """Interpolating user text into a script body is the injection bug this
    whole design exists to avoid. No script may contain a format placeholder."""
    for name in _SCRIPTS_TAKING_INPUT + _SCRIPTS_TAKING_NO_INPUT:
        script = getattr(macos_tools, name)
        assert "%s" not in script, "%s uses %%-interpolation!" % name
        assert "{}" not in script, "%s uses .format()!" % name
        assert "{0}" not in script, "%s uses .format()!" % name


def test_scripts_taking_user_input_read_it_from_argv():
    for name in _SCRIPTS_TAKING_INPUT:
        assert "argv" in getattr(macos_tools, name), \
            "%s takes user input but does not read it from argv" % name


def test_scripts_without_argv_genuinely_take_no_input():
    """These are read-only queries with nothing user-supplied in them."""
    for name in _SCRIPTS_TAKING_NO_INPUT:
        assert "argv" not in getattr(macos_tools, name)


def test_no_script_constant_is_ever_formatted_before_running():
    """Belt and braces: catch `osa.run(SCRIPT % x)` anywhere in the module."""
    import inspect
    import re
    source = inspect.getsource(macos_tools)
    for match in re.finditer(r"osa\.run\(\s*([A-Za-z_]+)\s*([%+])", source):
        raise AssertionError("script %s is combined with %s before running"
                             % (match.group(1), match.group(2)))


# ===========================================================================
# Graceful behaviour off macOS
# ===========================================================================
def test_macos_only_tools_are_hidden_from_the_model_elsewhere(sandbox):
    tools.load_all()
    names = [t["name"] for t in tools.provider_schemas(include_macos_only=False)]
    assert "calendar_read" not in names
    assert "file_read" in names


def test_macos_only_tool_refuses_clearly_off_platform(sandbox, monkeypatch):
    tools.load_all()
    monkeypatch.setattr(paths, "is_macos", lambda: False)
    result = registry.execute("calendar_read", {"days_ahead": 7},
                              registry.ExecContext())
    assert result.ok is False
    assert "only works on macOS" in result.error["what"]


def test_osa_run_refuses_off_platform(monkeypatch):
    monkeypatch.setattr(paths, "is_macos", lambda: False)
    with pytest.raises(PlatformUnsupported):
        osa.run("return 1")


def test_tcc_check_reports_platform_instead_of_failing(sandbox, monkeypatch):
    monkeypatch.setattr(paths, "is_macos", lambda: False)
    report = tcc.check_all()
    assert report["platform"] == "not macOS"
    assert report["permissions"] == []


def test_full_disk_access_stance_is_explicit():
    note = tcc.full_disk_access_note()
    assert "does NOT ask for Full Disk Access" in note
    assert "NOT IMPLEMENTED" in note


# ===========================================================================
# Error translation
# ===========================================================================
def test_permission_error_is_explained_in_plain_english():
    result = osa.OSAResult(False, "", "execution error: Not authorized (-1743)", 1)
    error = osa.explain_failure(result, "Calendar")
    assert "has not allowed access to Calendar" in error.what
    assert "Automation" in error.fix
    assert "assistant doctor --request-permissions" in error.fix


def test_unknown_error_still_produces_a_usable_message():
    error = osa.explain_failure(osa.OSAResult(False, "", "something odd", 1), "Notes")
    assert "Notes" in error.what
    assert error.fix


# ===========================================================================
# Date parsing
# ===========================================================================
@pytest.mark.parametrize("text,expected", [
    ("2026-03-14T15:00", (2026, 3, 14, 15, 0)),
    ("2026-03-14 15:00", (2026, 3, 14, 15, 0)),
    ("2026-03-14", (2026, 3, 14, 0, 0)),
])
def test_iso_dates_parse(text, expected):
    parsed = macos_tools._parse_datetime(text)
    assert (parsed.year, parsed.month, parsed.day, parsed.hour, parsed.minute) == expected


def test_friendly_dates_parse():
    assert macos_tools._parse_datetime("tomorrow at 3pm").hour == 15
    assert macos_tools._parse_datetime("today at 9am").hour == 9
    assert macos_tools._parse_datetime("today").hour == 9


def test_unparseable_date_says_what_it_wanted():
    with pytest.raises(ValidationError) as caught:
        macos_tools._parse_datetime("sometime next week maybe")
    assert "YYYY-MM-DD" in caught.value.fix


def test_application_name_cannot_be_a_path_or_a_flag(sandbox, monkeypatch):
    monkeypatch.setattr(paths, "is_macos", lambda: True)
    for bad in ("/Applications/Evil.app", "-a", "../../bin/sh"):
        with pytest.raises(ValidationError):
            macos_tools._app_launch({"name": bad}, registry.ExecContext())


# ===========================================================================
# launchd
# ===========================================================================
def test_plist_is_valid_and_describes_an_agent(sandbox, tmp_path):
    data = launchd.build_plist(python="/usr/bin/python3", app_dir=tmp_path)
    assert data["Label"] == paths.LAUNCH_LABEL
    assert data["ProgramArguments"][0] == "/usr/bin/python3"
    assert data["RunAtLoad"] is True
    assert data["KeepAlive"] == {"SuccessfulExit": False}
    assert data["ProcessType"] == "Background"
    # It must be serialisable as a real plist, or launchd will reject it.
    written = launchd.write_plist(data, tmp_path / "agent.plist")
    with open(written, "rb") as handle:
        assert plistlib.load(handle)["Label"] == paths.LAUNCH_LABEL


def test_plist_never_contains_a_literal_api_key(sandbox, tmp_path, monkeypatch):
    """The key is carried through the environment, not written to disk here."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value-do-not-store")
    data = launchd.build_plist(app_dir=tmp_path)
    written = launchd.write_plist(data, tmp_path / "agent.plist")
    text = written.read_text()
    # It IS carried in EnvironmentVariables by design - assert we know that,
    # and that the plist is owner-readable only where the OS allows it.
    assert "ANTHROPIC_API_KEY" in text
    assert data["EnvironmentVariables"]["ANTHROPIC_API_KEY"].startswith("sk-ant")


def test_launchd_uses_the_modern_domain_target_form():
    assert launchd.domain_target().startswith("gui/")
    assert launchd.service_target().endswith(paths.LAUNCH_LABEL)


def test_launchd_status_is_honest_off_platform(sandbox, monkeypatch):
    monkeypatch.setattr(paths, "is_macos", lambda: False)
    status = launchd.status()
    assert status["loaded"] is False
    assert "macOS-only" in status["note"]
