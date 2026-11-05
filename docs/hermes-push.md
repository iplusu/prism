# API push tracking (`X-Prism-Client`)

Lets a project page show that an external push (script/agent, not the browser editor) arrived and whether it worked.

## Client side

Add header `X-Prism-Client: <name>` to the five write endpoints: `POST .../files/upload`, `PUT .../files/content`, `POST .../files`, `POST .../files/rename`, `DELETE .../files`. Keep sending `X-Prism-CSRF: 1` and the session cookie as before.

```bash
curl -s -b "$JAR" -H 'X-Prism-CSRF: 1' -H 'X-Prism-Client: hermes' \
     -F file=@paper.tex -F directory=sections \
     "$PRISM/api/projects/$PID/files/upload"
```

`<name>`: 1-60 chars, `[A-Za-z0-9 .@:/+-]` (others stripped); empty after stripping falls back to the first token of `User-Agent`. Without the header, a `User-Agent` starting with `Mozilla/` is treated as the browser editor and never recorded — don't spoof a browser UA or run headless-browser automation against these endpoints if you want pushes tracked.

## Behavior

- **Batching**: successful writes by the same `(project, client)` within 60s of the last one merge into one activity row (`count` goes up, `paths` appends, capped at 20); past 60s idle, the next write starts a new row.
- **Success**: HTTP 200 on the write call itself. (The existing skill's `GET .../files`-then-compare-sizes verification is unaffected and still worth doing.)
- **Recorded failures**: 400 (bad file type/path), 409 (`overwrite=false` conflict, or `base_sha` mismatch), 413 (too large) — these show as a failed row with the error text. A 404 "file not found" from `rename`/`delete` is also recorded this way (it happens inside the same tracked block), even though it isn't in the three codes above.
- **Not recorded**: 401/403 (auth) and a project-level 404 (no such project / no access) — these are rejected by the route's auth dependency before the write is ever attempted, so there's nothing to log. Report these to the user from the HTTP status directly.
- No separate "push finished" call needed; the UI polls `GET /api/projects/{pid}/activity?limit=N` every 5s on its own while the project page is open.

## Verifying a push landed

```
GET /api/projects/{pid}/activity?limit=N
→ [{"id","client","ok","count","paths","error","created_at","updated_at"}]
```
Newest first. After a push, look for `client` matching what you sent, `ok: true`, `count` matching the number of files — that's confirmation Prism saw it, independent of the write calls' own 200s.

## Scope

Only file writes through this API are tracked. A `git push` straight to GitHub bypasses Prism entirely and is invisible here.
