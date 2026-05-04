from unittest.mock import MagicMock, patch
from cli import HermesCLI
from hermes_cli.rck_assisted import RCK_SESSION_META_KEY

class FakeSessionDB:
    def __init__(self):
        self.raw = None
    def get_meta(self, key):
        return self.raw if key == RCK_SESSION_META_KEY else None
    def set_meta(self, key, value):
        if key == RCK_SESSION_META_KEY:
            self.raw = value

cli = HermesCLI.__new__(HermesCLI)
cli.config = {"rck": {"command": "rck", "workspace": "/home/rufus/.rck"}}
cli.console = MagicMock()
cli._app = None
cli._session_db = FakeSessionDB()
cli._rck_session_state = None
cli._console_print = MagicMock()

call_log = []

def fake_run(config, subcommand, extra_args=None, timeout=60):
    extra_args = list(extra_args or [])
    call_log.append((subcommand, extra_args))
    if subcommand == "trace" and extra_args[:1] == ["start"]:
        return MagicMock(returncode=0, stdout="TraceId: assisted-state-smoke\n", stderr="")
    if subcommand == "state" and extra_args[:1] == ["add"]:
        return MagicMock(returncode=0, stdout="StateId: state-123\n", stderr="")
    if subcommand == "trace" and extra_args[:1] == ["show"]:
        return MagicMock(returncode=0, stdout="trace show assisted-state-smoke\n", stderr="")
    raise AssertionError((subcommand, extra_args))

with patch("hermes_cli.rck_assisted.run_rck_subcommand", side_effect=fake_run), patch("hermes_cli.rck.run_rck_subcommand", side_effect=fake_run):
    results = [
        ("init", cli.process_command("/rck init assisted-state-smoke --label \"Assisted State Smoke\"")),
        ("state", cli.process_command("/rck state \"Validated assisted state capture through Hermes.\"")),
        ("current", cli.process_command("/rck current")),
        ("trace show", cli.process_command("/rck trace show assisted-state-smoke")),
    ]

print("RESULTS")
for item in results:
    print(item)
print("CALLS", call_log)
print("RAW", cli._session_db.raw)
print("LAST_STATE", cli._rck_session_state.last_state_id if cli._rck_session_state else None)
print("CONSOLE")
for c in cli._console_print.call_args_list:
    print(c.args[0])
PY