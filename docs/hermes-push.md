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

File writes through this API are tracked by the header above. A `git push` to GitHub (including `iplusu/prism` main, where `projects/<name>/` lab material lives) bypasses Prism entirely and is invisible here; so is the `projects/` directory itself, which is separate from the Prism web projects.

## `git push` into a project repository

A push to the Prism project repository itself (`git push prism main`, the remote pointing at `<prism data dir>/projects/<project id>/repo`) needs `receive.denyCurrentBranch=updateInstead` in that repository's config (git otherwise refuses to update the checked-out branch; `updateInstead` also needs a clean working tree). Prism needs nothing from the pusher: it reads the repository's HEAD reflog for `push` entries and lists each push once, named `git push · <author of the pushed commit>`, with the number and paths of the changed files. No header and no extra call.

### Setting it up for a project (the usual mistake is pushing to GitHub instead)

Do not push from a clone of the application repository: its `origin` is GitHub, and it holds the app source with the papers under `projects/`, not at the root. A Prism project repository has the `.tex`/`.bib` files at its root, so work in a clone of the project repository itself:

```
git clone <ssh alias>:<prism data dir>/projects/<project id>/repo ~/papers/<name>
cd ~/papers/<name>
git remote rename origin prism          # `git push prism main` now goes to Prism
```

Every time: commit, `git pull --rebase prism main` (Prism adds its own Compile and "Apply proposal" commits in between), then `git push prism main`. Never `--force`: it deletes Prism's history. A rejection says `non-fast-forward` (pull --rebase, push again) or mentions unstaged changes (the project has unsaved edits in Prism: compile or save there, then push again). Check with `git ls-remote prism main` against the local HEAD; with the project page open, the notice and the History entry appear within about 5 seconds.

- Only successful pushes leave a trace on the Prism side. A push git refuses (dirty working tree, wrong branch, no setting) is seen only in the pusher's git output; report that to the user from there.
- After a push the project's working tree changes; open editors keep the old text until the file is reopened.
