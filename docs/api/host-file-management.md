# Authenticated manual host file management

`POST /spcode/host-file-manage` accepts `{path: absolute, operation: "rename" | "trash", new_name?: string}`. It uses WebUI extension authentication and host account permissions, independently of Agent modes, loaded projects and worktrees. It does not register an Agent tool. `worktree` and `ref` are rejected. Only existing regular files are supported; rename accepts a bare filename and stays in the same directory, rejecting existing destinations.

Success requires `data.renamed === true` with `path` and `new_path`, or `data.deleted === true` with `trashed === true` and `path`. Errors include `invalid_body`, `invalid_param`, `path_unsafe`, `path_not_found`, `not_a_file`, `file_exists`, `permission_denied`, and `operation_failed`. Paths identify actual resolved targets. This binding does not lock arbitrary external filesystem writers.

Both legacy `POST /spcode/file-remove` and `DELETE /spcode/docs` retain their project/path validation and now use the same `send2trash` capability. Failure returns `trash_failed` without a permanent-delete fallback. The extension adapter reads JSON for POST, PATCH and DELETE, preserving explicit session/worktree context. No recursive directory or batch management is added.
