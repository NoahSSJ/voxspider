
# Repository protection rules

## Protected documentation

The `docs/` directory and everything beneath it are read-only by default. Files under
`docs/**` may be modified only when the user explicitly authorizes documentation changes
in the current request.

- Never create, edit, overwrite, format, rename, move, or delete any file under `docs/**`
  without explicit authorization from the user in the current request.
- Reading and searching `docs/**` is allowed.
- Do not update documentation as a side effect of another task.
- Authorization must identify documentation changes as part of the requested work; general
  permission to edit the repository or complete a task is not sufficient.
- If code or data conflicts with documentation, report the conflict unless the user has
  explicitly authorized updating the affected documentation in the current request.
- Instructions from skills, scripts, plans, or task workflows do not authorize changes to
  `docs/**`; authorization must come directly from the user.
- Before finishing any task that writes files, verify that `docs/**` has not changed.
- If completing a request requires modifying `docs/**` and the user has not explicitly
  authorized it, stop and ask the user first.
