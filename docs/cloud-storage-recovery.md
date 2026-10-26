# Read-only observer storage recovery

This is an operational storage intervention for an existing frozen paper account. It does not replace its source, configuration, model, balances, run identity, first accepted quote or seven-day deadline.

The live collector opens and closes SQLite connections frequently. In WAL mode, SQLite normally removes its auxiliary WAL and shared-memory files after its last connection closes. An observer with a read-only volume can then fail to open the database because it cannot create those auxiliary files. A static prepared account can miss this failure during a preflight test. See [SQLite read-only WAL requirements](https://sqlite.org/wal.html#read_only_databases).

The separate tools/repair_cloud_storage.py helper uses SQLite's supported PRAGMA journal_mode=DELETE operation. DELETE is SQLite's standard rollback journal mode; the operation changes storage journaling, not accounting rows. It retains crash-safe journaling and the observer's read-only mount. See [SQLite journal modes](https://sqlite.org/pragma.html#pragma_journal_mode).

Run the helper only through the paper container's existing writable volume. It first checks the frozen fingerprint, saves an independently audited online backup, and records a dated maintenance receipt. It then verifies database integrity, the unchanged account and frozen manifests, and all four accounting books. Busy storage is retried briefly; failures retain their evidence and do not reset anything. A separate read from the actual adviser container must still verify access and a later scheduled model receipt.

Do not pull a new checkout or rebuild frozen containers to install this helper. An operator may download the single reviewed helper from an exact Git commit, verify its SHA-256 and copy it into ignored runtime maintenance storage. Its imports use the unchanged cloud tool already in the image. The server's pinned source commit remains unchanged.

The Docker regression reproduces the WAL failure on a genuinely read-only Linux volume, applies recovery on the writer side, rejects writes from the reader side, verifies subsequent collection, and retains the original account clock. Its data are synthetic and count as zero prospective evidence. Local unit tests also exercise concurrent quote processing and preserve pre-repair backups.

The earlier observer failure remains part of the experiment history. Successful storage recovery does not prove advice quality, improved returns, a completed week, or future uptime.
