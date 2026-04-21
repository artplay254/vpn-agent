import sqlite3
import time
import math
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any, Callable, TypeVar
from dataclasses import dataclass


DECAY_GRACE_PERIOD_SECONDS = 24 * 60 * 60
DECAY_LAMBDA = 1.0 / (24 * 60 * 60)
T = TypeVar("T")


@dataclass
class ConfigEntry:
    id: int
    protocol: str
    config_hash: str
    alias: str
    mtu: int
    is_mutation: bool
    parent_hash: Optional[str]


@dataclass
class BestConfig:
    config_hash: str
    protocol: str
    alias: str
    reliability_score: float
    success_rate: float
    avg_latency: float
    total_attempts: int
    last_updated: float
    recency_weight: float
    decay_applied: bool


class BrainDatabase:
    """SQLite database for storing VPN configuration performance data."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database tables if they don't exist."""
        with self._get_connection() as conn:
            conn.execute('PRAGMA journal_mode=WAL;')
            conn.execute('PRAGMA synchronous=NORMAL;')
            # `configs` stores immutable-ish facts about a config variant itself.
            # Per-attempt results belong in `metrics`.
            conn.execute('''
                CREATE TABLE IF NOT EXISTS configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    protocol TEXT NOT NULL,
                    config_hash TEXT UNIQUE NOT NULL,
                    alias TEXT NOT NULL,
                    mtu INTEGER NOT NULL,
                    is_mutation BOOLEAN NOT NULL DEFAULT FALSE,
                    parent_hash TEXT,
                    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
                    last_connected REAL
                )
            ''')

            conn.execute('''
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    config_id INTEGER NOT NULL,
                    timestamp REAL NOT NULL,
                    network_id TEXT NOT NULL,
                    success BOOLEAN NOT NULL,
                    latency REAL,
                    error_type TEXT,
                    port INTEGER,
                    is_stale BOOLEAN NOT NULL DEFAULT FALSE,
                    FOREIGN KEY (config_id) REFERENCES configs (id)
                )
            ''')

            # Ranking queries repeatedly filter by network and join on config_id,
            # so these indexes keep the "brain" responsive as history grows.
            conn.execute('CREATE INDEX IF NOT EXISTS idx_metrics_config_network ON metrics (config_id, network_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics (timestamp)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_configs_hash ON configs (config_hash)')

            conn.execute('''
                CREATE TABLE IF NOT EXISTS network_mtu (
                    network_id TEXT PRIMARY KEY,
                    mtu INTEGER NOT NULL,
                    discovered_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
                )
            ''')

            # Lightweight in-place migrations let existing users upgrade without
            # rebuilding the DB by hand.
            existing = [row['name'] for row in conn.execute('PRAGMA table_info(metrics)')]
            if 'port' not in existing:
                conn.execute('ALTER TABLE metrics ADD COLUMN port INTEGER')
            if 'is_stale' not in existing:
                conn.execute('ALTER TABLE metrics ADD COLUMN is_stale BOOLEAN NOT NULL DEFAULT FALSE')

            config_columns = [row['name'] for row in conn.execute('PRAGMA table_info(configs)')]
            if 'last_connected' not in config_columns:
                conn.execute('ALTER TABLE configs ADD COLUMN last_connected REAL')

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection with row factory and timeout."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        # Re-apply connection-level pragmas every time because SQLite scopes them
        # to the current connection, not the whole database file.
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA synchronous=NORMAL;')
        conn.execute('PRAGMA foreign_keys=ON;')
        return conn

    def _run_with_retry(self, operation: Callable[[sqlite3.Connection], T], max_retries: int = 3) -> T:
        """Run a database operation with retry logic for transient locks."""
        for attempt in range(max_retries):
            try:
                with self._get_connection() as conn:
                    return operation(conn)
            except sqlite3.OperationalError as e:
                # WAL reduces lock pressure, but we can still briefly collide with
                # another writer. A short exponential backoff is enough here.
                if "database is locked" in str(e) and attempt < max_retries - 1:
                    time.sleep(0.1 * (2 ** attempt))  # Exponential backoff
                    continue
                raise
        raise RuntimeError("Database operation failed after retries")

    def _execute_with_retry(self, query: str, params: Tuple = (), max_retries: int = 3) -> list[sqlite3.Row]:
        """Execute a query and materialize all rows before the connection closes."""
        def operation(conn: sqlite3.Connection) -> list[sqlite3.Row]:
            cursor = conn.execute(query, params)
            return cursor.fetchall()

        return self._run_with_retry(operation, max_retries=max_retries)

    def register_config(self, protocol: str, config_hash: str, alias: str,
                       mtu: int, is_mutation: bool, parent_hash: Optional[str] = None) -> int:
        """Register a new config or return existing config_id if hash already exists.

        Returns the config_id of the registered config.
        """
        def operation(conn: sqlite3.Connection) -> int:
            # Content hash is the canonical identity. If the same config appears
            # again later, we reuse the existing row instead of duplicating it.
            existing = conn.execute(
                'SELECT id FROM configs WHERE config_hash = ?',
                (config_hash,)
            ).fetchone()
            if existing:
                return int(existing['id'])

            # `lastrowid` must be read from the same connection that performed the
            # insert, so this lives inside the callback instead of a separate query.
            cursor = conn.execute(
                '''INSERT INTO configs (protocol, config_hash, alias, mtu, is_mutation, parent_hash)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (protocol, config_hash, alias, mtu, is_mutation, parent_hash)
            )
            return int(cursor.lastrowid)

        return self._run_with_retry(operation)

    def log_attempt(self, config_id: int, timestamp: float, network_id: str,
                   success: bool, latency: Optional[float], error_type: Optional[str] = None,
                   port: Optional[int] = None, is_stale: bool = False) -> int:
        """Log the result of a connection attempt."""
        def operation(conn: sqlite3.Connection) -> int:
            # Every connection attempt gets its own row. This is the raw event log
            # that later gets aggregated into reliability scores.
            cursor = conn.execute(
                '''INSERT INTO metrics (config_id, timestamp, network_id, success, latency, error_type, port, is_stale)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (config_id, timestamp, network_id, success, latency, error_type, port, is_stale)
            )
            # `last_connected` is a convenience field for quick "recent activity"
            # checks without scanning the whole metrics table.
            conn.execute(
                'UPDATE configs SET last_connected = ? WHERE id = ?',
                (timestamp, config_id)
            )
            return int(cursor.lastrowid)

        return self._run_with_retry(operation)

    def mark_metric_stale(self, metric_id: int, error_type: Optional[str] = None) -> None:
        """Mark an existing metric entry as stale so it is excluded from scoring."""
        # We keep the row for debugging/history, but remove its influence on future
        # ranking so one false-positive success does not mislead the selector.
        if error_type:
            self._execute_with_retry(
                '''UPDATE metrics
                   SET is_stale = 1,
                       error_type = ?
                   WHERE id = ?''',
                (error_type, metric_id)
            )
            return

        self._execute_with_retry(
            'UPDATE metrics SET is_stale = 1 WHERE id = ?',
            (metric_id,)
        )

    def _recency_weight(self, last_updated: float, now: Optional[float] = None) -> Tuple[float, bool]:
        """Return the freshness multiplier and whether decay was applied."""
        now_ts = time.time() if now is None else now
        age_seconds = max(0.0, now_ts - last_updated)
        # Fresh observations stay "full power" for a while so the system does not
        # instantly distrust a config just because a few hours passed.
        if age_seconds <= DECAY_GRACE_PERIOD_SECONDS:
            return 1.0, False

        decayed_age = age_seconds - DECAY_GRACE_PERIOD_SECONDS
        return math.exp(-DECAY_LAMBDA * decayed_age), True

    def _score_from_stats(self, success_count: int, total_attempts: int,
                         avg_latency: Optional[float], last_updated: float,
                         now: Optional[float] = None) -> Tuple[float, float, float, float, bool]:
        # Scoring is intentionally simple and explainable:
        # success matters most, latency is a tiebreaker, and old data decays.
        success_rate = success_count * 1.0 / total_attempts if total_attempts else 0.0
        latency_factor = (
            0.25
            if avg_latency is None
            else (1.0 - min(1.0, max(0.0, (avg_latency - 20.0) / 480.0)))
        )
        recency_weight, decay_applied = self._recency_weight(last_updated, now=now)
        reliability_score = ((success_rate * 0.7) + (latency_factor * 0.3)) * recency_weight
        return reliability_score, success_rate, latency_factor, recency_weight, decay_applied

    def get_ranked_configs(self, network_id: str, protocol: Optional[str] = None) -> List[BestConfig]:
        """Return all scored configs for a network, ranked by reliability."""
        query = '''
            SELECT
                c.protocol,
                c.config_hash,
                c.alias,
                COUNT(m.id) as total_attempts,
                SUM(CASE WHEN m.success THEN 1 ELSE 0 END) as success_count,
                AVG(m.latency) as avg_latency,
                MAX(m.timestamp) as last_updated
            FROM configs c
            JOIN metrics m ON c.id = m.config_id
            WHERE m.network_id = ?
              AND COALESCE(m.is_stale, 0) = 0
        '''
        params: list[Any] = [network_id]
        if protocol is not None:
            # Reuse the same query for "all protocols" and "just this protocol"
            # so the ranking rules stay identical everywhere in the app.
            query += ' AND c.protocol = ?'
            params.append(protocol)

        query += '''
            GROUP BY c.id, c.protocol, c.config_hash, c.alias
            HAVING total_attempts >= 1
        '''

        rows = self._execute_with_retry(query, tuple(params))
        now_ts = time.time()
        ranked: List[BestConfig] = []
        for row in rows:
            reliability_score, success_rate, _, recency_weight, decay_applied = self._score_from_stats(
                row['success_count'] or 0,
                row['total_attempts'],
                row['avg_latency'],
                row['last_updated'],
                now=now_ts,
            )
            ranked.append(
                BestConfig(
                    config_hash=row['config_hash'],
                    protocol=row['protocol'],
                    alias=row['alias'],
                    reliability_score=reliability_score,
                    success_rate=success_rate,
                    avg_latency=row['avg_latency'] if row['avg_latency'] is not None else 0.0,
                    total_attempts=row['total_attempts'],
                    last_updated=row['last_updated'],
                    recency_weight=recency_weight,
                    decay_applied=decay_applied,
                )
            )

        # Sort in Python after the decay calculation because recency weighting is
        # time-dependent and easier to express here than in raw SQL.
        return sorted(ranked, key=lambda entry: (-entry.reliability_score, -entry.total_attempts, entry.alias))

    def get_best_config(self, network_id: str) -> Optional[BestConfig]:
        """Get the best config for a network based on reliability score.

        Reliability Score = success_rate * 0.7 + latency_factor * 0.3
        where latency_factor = 1.0 - min(1.0, max(0.0, (avg_latency - 20) / 480))
        """
        ranked = self.get_ranked_configs(network_id)
        return ranked[0] if ranked else None

    def list_network_ids(self) -> list[str]:
        """Return known network contexts stored in metrics."""
        rows = self._execute_with_retry('SELECT DISTINCT network_id FROM metrics')
        return [row['network_id'] for row in rows]

    def get_network_stats(self) -> list[dict[str, Any]]:
        """Return summary statistics for each known network context."""
        networks = self.list_network_ids()
        stats: list[dict[str, Any]] = []
        for network_id in networks:
            best = self.get_best_config(network_id)
            if not best:
                continue
            stats.append({
                'network_id': network_id,
                'protocol': best.protocol,
                'config_alias': best.alias,
                'reliability_score': best.reliability_score,
                'success_rate': best.success_rate,
                'avg_latency': best.avg_latency,
            })
        return stats

    def get_config_score(self, config_hash: str, network_id: str) -> Optional[Tuple[float, float, float, int]]:
        """Get the reliability score for a config on a specific network."""
        query = '''
            WITH config_stats AS (
                SELECT
                    COUNT(m.id) as total_attempts,
                    SUM(CASE WHEN m.success THEN 1 ELSE 0 END) as success_count,
                    AVG(m.latency) as avg_latency,
                    MAX(m.timestamp) as last_updated
                FROM configs c
                JOIN metrics m ON c.id = m.config_id
                WHERE c.config_hash = ?
                  AND m.network_id = ?
                  AND COALESCE(m.is_stale, 0) = 0
            )
            SELECT
                total_attempts,
                success_count,
                avg_latency,
                last_updated
            FROM config_stats
        '''
        rows = self._execute_with_retry(query, (config_hash, network_id))
        row = rows[0] if rows else None
        if row and row['total_attempts'] > 0:
            reliability_score, success_rate, _, _, _ = self._score_from_stats(
                row['success_count'] or 0,
                row['total_attempts'],
                row['avg_latency'],
                row['last_updated'],
            )
            return (reliability_score, success_rate, row['avg_latency'] if row['avg_latency'] is not None else 0.0, row['total_attempts'])
        return None

    def get_parent_and_previous_scores(self, network_id: str, parent_hash: str) -> Optional[Tuple[ConfigEntry, float, ConfigEntry, float]]:
        """Compare a parent config's score with its immediate predecessor on the same network."""
        parent = self.get_config_by_hash(parent_hash)
        if not parent or not parent.parent_hash:
            return None

        parent_score_data = self.get_config_score(parent_hash, network_id)
        previous_score_data = self.get_config_score(parent.parent_hash, network_id)

        if not parent_score_data or not previous_score_data:
            return None

        return (
            parent,
            parent_score_data[0],
            self.get_config_by_hash(parent.parent_hash),
            previous_score_data[0],
        )

    def get_risky_ports(self, network_id: str) -> List[int]:
        """Return ports marked as risky for a network based on connection refused errors."""
        query = '''
            SELECT DISTINCT port
            FROM metrics
            WHERE network_id = ?
              AND port IS NOT NULL
              AND LOWER(error_type) LIKE '%connection refused%'
        '''
        rows = self._execute_with_retry(query, (network_id,))
        return [row['port'] for row in rows if row['port'] is not None]

    def get_config_by_hash(self, config_hash: str) -> Optional[ConfigEntry]:
        """Get config details by hash."""
        rows = self._execute_with_retry(
            'SELECT * FROM configs WHERE config_hash = ?',
            (config_hash,)
        )
        row = rows[0] if rows else None
        if row:
            return ConfigEntry(
                id=row['id'],
                protocol=row['protocol'],
                config_hash=row['config_hash'],
                alias=row['alias'],
                mtu=row['mtu'],
                is_mutation=row['is_mutation'],
                parent_hash=row['parent_hash']
            )
        return None

    def get_network_mtu(self, network_id: str) -> Optional[int]:
        """Return the cached MTU for a network context, if available."""
        rows = self._execute_with_retry(
            'SELECT mtu FROM network_mtu WHERE network_id = ?',
            (network_id,)
        )
        row = rows[0] if rows else None
        return int(row['mtu']) if row else None

    def save_network_mtu(self, network_id: str, mtu: int) -> None:
        """Persist the discovered MTU for a network context."""
        self._execute_with_retry(
            '''INSERT OR REPLACE INTO network_mtu (network_id, mtu, discovered_at)
               VALUES (?, ?, ?)''',
            (network_id, mtu, time.time())
        )

    def migrate_json_metrics(self, json_metrics_path: Path) -> None:
        """Migrate existing JSON metrics to the database."""
        if not json_metrics_path.exists():
            return

        import json

        with open(json_metrics_path, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    metric = json.loads(line)
                    config_hash = metric.get('config_hash')
                    if not config_hash:
                        continue

                    # Register config if not exists
                    config_id = self.register_config(
                        protocol=metric.get('protocol', 'unknown'),
                        config_hash=config_hash,
                        alias=f"migrated_{config_hash[:8]}",
                        mtu=metric.get('mtu', 1280),
                        is_mutation=False
                    )

                    # Log the attempt
                    latency_str = metric.get('latency', 'N/A')
                    latency = None
                    if latency_str != 'N/A':
                        import re
                        match = re.search(r'(\d+(?:\.\d+)?)', latency_str)
                        if match:
                            latency = float(match.group(1))

                    self.log_attempt(
                        config_id=config_id,
                        timestamp=metric.get('timestamp', time.time()),
                        network_id=metric.get('network_id', 'network:unknown'),
                        success=metric.get('success', False),
                        latency=latency,
                        error_type=metric.get('error_msg') if not metric.get('success') else None
                    )
                except (json.JSONDecodeError, KeyError):
                    continue
