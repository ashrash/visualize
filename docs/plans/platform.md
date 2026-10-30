# Platform plan (Wave 1, MVP §10 backend)

Owner: platform. All routes keep the existing router-level `auth.current_user` dependency.
Errors reuse `viz.errors` (NotFound 404, Conflict 409, InvalidSpec 422).

## 1. Endpoints (prefix `/api/projects`)
| Method + path | Request | Response |
|---|---|---|
| DELETE `/{id}` | - | 204. Deletes the project and its versions (FK cascade). Datasets stay in the catalog (shared). |
| PATCH `/{id}` | `ProjectRename {name, revision}` | `Project`. Revision check; bumps revision; no snapshot. |
| POST `/{id}/duplicate` | `ProjectDuplicate {name?}` (default "<name> copy") | `Project` (201). New project id, revision 0, current doc copied. Inner ids (charts, dashboards) are kept: they are scoped to the project. No history copied. |
| PUT `/{id}?autosave=bool&label=str` (changed) | `Project` | `Project`. Adds validation (§3) and the snapshot policy (§2). `label` (explicit saves only) makes a named snapshot that is never pruned. |
| GET `/{id}/versions?kind=save\|autosave&limit=` (changed) | - | `ProjectVersion[]`, newest first. Adds optional filters. |
| GET `/{id}/versions/{versionId}` | - | `Project`: the snapshot doc, upgraded via migrations (read-only preview). |
| POST `/{id}/versions/{versionId}/restore` | `ProjectRestore {revision}` | `Project`. Revision check against the live revision. Writes the snapshot doc as revision+1 and adds a `save` snapshot labelled "Restored from r<N>". History is never rewritten. |
| GET `/{id}/export?includeData=bool` | - | `application/zip`, `Content-Disposition: <name>.vizproj`. |
| POST `/import` | multipart `file` (.vizproj) | `ProjectImportResult`, 201. |

- Duplicating charts/dashboards inside a project is purely client-side on the project doc (new `crypto.randomUUID()` ids, then autosave). No endpoint.
- Renaming the open project is just `name` in the doc via PUT; PATCH is for renaming from the project list without loading it.

## 2. Snapshot and autosave policy
- Explicit save: always a `save` snapshot (kept; resets the autosave counter).
- Autosave: updates the live doc; every Nth autosave since the last snapshot also writes an `autosave` snapshot. `VIZ_AUTOSAVE_SNAPSHOT_EVERY` (default 10, 0 disables).
- Pruning: after writing an autosave snapshot, keep the newest `VIZ_AUTOSAVE_SNAPSHOTS_KEEP` (default 20) `autosave` snapshots per project; delete older ones. `save` and labelled snapshots are never auto-pruned.
- Counter lives in the DB (`projects.autosaves_since_snapshot`), so it survives restarts and works across ECS tasks.
- A no-op autosave (doc equal to stored, ignoring revision/updatedAt) is skipped: returns the stored project, no revision bump.

## 3. Server-side validation on save/import (422, clear message)
Folder ids unique; `parentId` exists, no cycles; every `ItemRef` points at an existing dataset_id/pipeline/chart/dashboard; an item is in at most one folder; ids unique within pipelines/charts/dashboards. Unknown `dataset_ids` are not rejected (a catalog dataset may be deleted); on import they go to `datasetsMissing`.

## 4. Contract additions (additive; lead adds to `contracts/project.py` and the package exports)
```python
class ProjectRename(Contract):
    name: str
    revision: int


class ProjectDuplicate(Contract):
    name: str | None = None


class ProjectRestore(Contract):
    revision: int  # live revision the restore is based on


class ProjectImportResult(Contract):
    project: Project
    datasets_imported: list[str]  # dataset ids registered from the archive
    datasets_remapped: dict[str, str] = Field(default_factory=dict)  # archived id -> new id
    datasets_missing: list[str] = Field(
        default_factory=list
    )  # referenced, in neither archive nor catalog
    warnings: list[str] = Field(default_factory=list)
```
`ProjectVersion` is unchanged (non-null `label` = named snapshot). No `schemaVersion` bump.

## 5. Settings (config.py, mine)
`autosave_snapshot_every: int = 10`, `autosave_snapshots_keep: int = 20`, `max_vizproj_mb: int = 8192` (also caps total uncompressed size and entry count: zip-bomb guard).

## 6. DB / Alembic
`0002_project_autosave_counter`: add `projects.autosaves_since_snapshot INTEGER NOT NULL` with `server_default 0` (via `batch_alter_table`, so SQLite and Postgres both work); add index `ix_project_versions_project_kind_rev (project_id, kind, revision)` for pruning. Tested up/down on SQLite; plain Alembic ops, Postgres-safe.

## 7. `.vizproj` format (zip; .parquet stored uncompressed, JSON deflated)
```
manifest.json  {"format":"vizproj","formatVersion":1,"appVersion":"..","schemaVersion":1,"exportedAt":"..",
                "includesData":true,"datasets":[{"id":"..","name":"..","versions":[{"n":3,"file":"datasets/<id>/v3.parquet"}]}]}
project.json   the Project doc (camelCase), as stored
datasets/<id>/dataset.json   Dataset doc (catalog entry)
datasets/<id>/v<n>.parquet   only with includeData; latest version only
```
- Versioning: `formatVersion` is an int; import accepts <= current, rejects newer with 409. `project.json` goes through `upgrade_project`, so old exports keep loading.
- Import: always a new project id, revision 0. Datasets: id already in catalog with the same latest rowCount and columns -> reuse; id exists but differs -> register under a new id and remap references (`dataset_ids`, pipeline `datasetId`, chart `source`); id absent -> register as-is; absent with no data in the archive -> `datasetsMissing`.
- Safety: reject absolute paths, `..`, backslashes and symlinks in entry names (zip-slip); enforce size/entry caps; never trust `parquetPath` from dataset.json (the catalog rewrites it).
- Export reads storage only through `LocalStorage.resolve(rel)`.

## 8. Needs from data-core (their area; I stub with `TODO(data-core)` until it lands)
`catalog.register_dataset_bundle(db, storage, dataset: Dataset, parquet_files: dict[int, Path], *, new_id: str | None = None) -> Dataset`: copies files to `datasets/<id>/v<n>.parquet`, rewrites `parquetPath`, saves the catalog row (optionally under a new id). Until then I fall back to `LocalStorage.parquet_rel` + `catalog.save_dataset`. `get_dataset` remains the read API.

## 9. What ui-data needs to know
- 409 on a stale revision (save, rename, restore): reload and retry. Restore/rename/duplicate return the full `Project` to swap into the store; after restore, reset zundo history.
- Autosave returns the new `revision` (or the unchanged one for a no-op): always adopt it.
- Export: plain `<a href="/api/projects/{id}/export?includeData=true">`. Import: `multipart/form-data`, field `file`.
- Versions list carries `kind` and `label`; preview a version with `GET .../versions/{versionId}`.
- Chart/dashboard duplication is client-side only.
- 422 bodies carry a message string; show it verbatim.

## 10. Test plan (`backend/tests/projects/`, `backend/tests/meta/`)
Known-answer tests first: autosave counter and snapshot cadence at N=3; pruning keeps newest K and never touches `save`/labelled; restore gives revision+1 with history intact; stale revision -> 409 on PUT/PATCH/restore; delete cascades versions but not datasets; duplicate has new id, revision 0, same content; folder validation (dangling ref, cycle, double membership); upgrader path (monkeypatched fake v0->v1 upgrader) on get/preview/import; newer schemaVersion -> 409; `.vizproj` round trip with and without data (Parquet bytes equal), paths with spaces/non-ASCII, zip-slip and oversize rejection, id-collision remap; Alembic 0001->0002 up/down on SQLite; settings env override. Router tests via TestClient with a temp `VIZ_DATA_DIR`. Finish with ruff, mypy, pytest and a `viz serve` smoke run on a free port.
