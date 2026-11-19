# Python type checking

`make typecheck` runs Django-aware mypy on `src`, including tests and migrations.
The same check runs in `make lint` and in the Docker lint service used by CI.
Mypy, django-stubs and Channels stubs are pinned in `pyproject.toml` and `uv.lock`.

The Django URL `path` type only accepts HTTP views, while Channels accepts ASGI
applications and its router stub describes a narrower URL pattern type. The
WebSocket routing boundary and its corresponding test therefore use targeted
`arg-type` ignores. A test also uses a targeted `attr-defined` ignore for the
`__wrapped__` attribute supplied by Django's view decorators.

Mypy does not fully check unannotated function bodies with the current settings.
