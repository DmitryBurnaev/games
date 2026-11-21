# Session lifetime

Sessions use Django's database backend and a persistent browser cookie. Successful
HTTP requests carrying a session renew both the database expiry and cookie for
30 days; this includes game actions and polling, but not WebSocket messages or
heartbeats. A continuously open WebSocket alone is not activity for this policy.
There is no absolute lifetime while HTTP activity continues. Closing a browser
does not intentionally end a session. Logout, a password change, and deletion of
the server-side session still invalidate it.

Previously Django's default two-week cookie and server expiry were not renewed
on every request. That can explain a logout despite daily visits, although the
specific reported incident cannot be diagnosed from the available evidence.
Existing, unexpired database sessions are renewed on their next HTTP request;
expired sessions require login. Browser cookie eviction and clock differences
can also end a session independently of this policy.

Application restarts and deployments preserve sessions only if they retain the
same SQLite database and `DJANGO_SECRET_KEY`. Production requires the key, and
the deployment mounts the database directory; changing either invalidates or
loses sessions. The key is also used for Django's authentication session hash.
