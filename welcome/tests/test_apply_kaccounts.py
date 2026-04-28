from unittest.mock import MagicMock

from bluefox_welcome.apply import kaccounts as kaccounts_mod


def test_apply_kaccounts_runs_kcmshell6(monkeypatch, tenant_data: dict):
    monkeypatch.setattr(kaccounts_mod.shutil, "which",
                        lambda binary: "/usr/bin/kcmshell6" if binary == "kcmshell6" else None)
    runner = MagicMock()
    ok, msg = kaccounts_mod.apply_kaccounts(tenant_data, runner=runner)
    assert ok, msg
    runner.assert_called_once()
    args = runner.call_args.args[0]
    assert args == ["/usr/bin/kcmshell6", "kcm_kaccounts"]


def test_apply_kaccounts_missing_binary_fails(monkeypatch, tenant_data: dict):
    monkeypatch.setattr(kaccounts_mod.shutil, "which", lambda _: None)
    ok, msg = kaccounts_mod.apply_kaccounts(tenant_data, runner=MagicMock())
    assert not ok
    assert "kcmshell6" in msg


def test_apply_kaccounts_runner_exception(monkeypatch, tenant_data: dict):
    monkeypatch.setattr(kaccounts_mod.shutil, "which", lambda _: "/usr/bin/kcmshell6")
    def explode(*a, **kw):
        raise OSError("boom")
    ok, msg = kaccounts_mod.apply_kaccounts(tenant_data, runner=explode)
    assert not ok
    assert "boom" in msg
