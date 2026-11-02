"""Cooperative cancellation shared by worker and legacy media subprocesses."""

from contextvars import ContextVar

_check = ContextVar("clipforge_job_cancel_check", default=None)


def install(check):
    return _check.set(check)


def reset(token):
    _check.reset(token)


def check_cancel():
    check = _check.get()
    if check is not None:
        check()
