"""Which staged uploads an import or refresh job is reading right now (this process).

`DELETE /datasets/staged/{id}` refuses (409) while a job uses the upload: on Windows the
open file can't be deleted (the folder would silently stay), and on POSIX the job would
fail halfway. The upload is marked for the whole job, because the source copy in
`sources/` is a hard link to the same file. A queued job that finds its upload already
deleted fails with "the uploaded file was deleted".
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager

_lock = threading.Lock()
_in_use: Counter[str] = Counter()


@contextmanager
def using_staged(staged_id: str | None) -> Iterator[None]:
    if staged_id is None:
        yield
        return
    with _lock:
        _in_use[staged_id] += 1
    try:
        yield
    finally:
        with _lock:
            _in_use[staged_id] -= 1
            if _in_use[staged_id] <= 0:
                del _in_use[staged_id]


def staged_in_use(staged_id: str) -> bool:
    with _lock:
        return _in_use.get(staged_id, 0) > 0
