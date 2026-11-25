"""The sample campaign is only for the very first start: once a sign-in is enrolled, an emptied database stays empty."""
from clipping import __main__ as entry


class FakeDb:
    dsn = "unused"


def test_first_start_seeds_only_before_a_sign_in_exists(monkeypatch):
    import psycopg

    class Conn:
        def __init__(self, n): self.n = n
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql):
            assert "auth_credentials" in sql
            return type("R", (), {"fetchone": lambda s: (self.n,)})()

    monkeypatch.setattr(psycopg, "connect", lambda dsn: Conn(0))
    assert entry._first_start(FakeDb()) is True
    monkeypatch.setattr(psycopg, "connect", lambda dsn: Conn(1))
    assert entry._first_start(FakeDb()) is False
    monkeypatch.setattr(psycopg, "connect", lambda dsn: (_ for _ in ()).throw(RuntimeError("no table")))
    assert entry._first_start(FakeDb()) is True
