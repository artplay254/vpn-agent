import sqlite3
import time
import os
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
from dataclasses import dataclass


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
            conn.execute('''
                CREATE TABLE IF NOT EXISTS configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    protocol TEXT NOT NULL,
                    config_hash TEXT UNIQUE NOT NULL,
                    alias TEXT NOT NULL,
                    mtu INTEGER NOT NULL,
                    is_mutation BOOLEAN NOT NULL DEFAULT FALSE,
                    parent_hash TEXT,
                    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
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
                    FOREIGN KEY (config_id) REFERENCES configs (id)
                )
            ''')

            # Create indexes for performance
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

            # Migrate older schemas by adding missing columns
            existing = [row['name'] for row in conn.execute('PRAGMA table_info(metrics)')]
            if 'port' not in existing:
                conn.execute('ALTER TABLE metrics ADD COLUMN port INTEGER')

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection with row factory and timeout."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _execute_with_retry(self, query: str, params: Tuple = (), max_retries: int = 3) -> sqlite3.Cursor:
        """Execute a query with retry logic for database locks."""
        for attempt in range(max_retries):
            try:
                with self._get_connection() as conn:
                    return conn.execute(query, params)
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < max_retries - 1:
                    time.sleep(0.1 * (2 ** attempt))  # Exponential backoff
                    continue
                raise

    def register_config(self, protocol: str, config_hash: str, alias: str,
                       mtu: int, is_mutation: bool, parent_hash: Optional[str] = None) -> int:
        """Register a new config or return existing config_id if hash already exists.

        Returns the config_id of the registered config.
        """
        # Check if config already exists
        cursor = self._execute_with_retry(
            'SELECT id FROM configs WHERE config_hash = ?',
            (config_hash,)
        )
        existing = cursor.fetchone()
        if existing:
            return existing[0]

        # Insert new config
        cursor = self._execute_with_retry(
            '''INSERT INTO configs (protocol, config_hash, alias, mtu, is_mutation, parent_hash)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (protocol, config_hash, alias, mtu, is_mutation, parent_hash)
        )

        # Get the inserted row id
        cursor = self._execute_with_retry('SELECT last_insert_rowid()')
        return cursor.fetchone()[0]

    def log_attempt(self, config_id: int, timestamp: float, network_id: str,
                   success: bool, latency: Optional[float], error_type: Optional[str] = None,
                   port: Optional[int] = None) -> None:
        """Log the result of a connection attempt."""
        self._execute_with_retry(
            '''INSERT INTO metrics (config_id, timestamp, network_id, success, latency, error_type, port)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (config_id, timestamp, network_id, success, latency, error_type, port)
        )

    def get_best_config(self, network_id: str) -> Optional[BestConfig]:
        """Get the best config for a network based on reliability score.

        Reliability Score = success_rate * 0.7 + latency_factor * 0.3
        where latency_factor = 1.0 - min(1.0, max(0.0, (avg_latency - 20) / 480))
        """
        query = '''
            WITH config_stats AS (
                SELECT
                    c.id,
                    c.protocol,
                    c.config_hash,
                    c.alias,
                    COUNT(m.id) as total_attempts,
                    SUM(CASE WHEN m.success THEN 1 ELSE 0 END) as success_count,
                    AVG(m.latency) as avg_latency
                FROM configs c
                JOIN metrics m ON c.id = m.config_id
                WHERE m.network_id = ?
                GROUP BY c.id, c.protocol, c.config_hash, c.alias
                HAVING total_attempts >= 1
            ),
            scored_configs AS (
                SELECT
                    *,
                    (success_count * 1.0 / total_attempts) as success_rate,
                    CASE
                        WHEN avg_latency IS NULL THEN 0.25
                        ELSE (1.0 - MIN(1.0, MAX(0.0, (avg_latency - 20.0) / 480.0)))
                    END as latency_factor,
                    ((success_count * 1.0 / total_attempts) * 0.7 +
                     CASE
                         WHEN avg_latency IS NULL THEN 0.25
                         ELSE (1.0 - MIN(1.0, MAX(0.0, (avg_latency - 20.0) / 480.0)))
                     END * 0.3) as reliability_score
                FROM config_stats
            )
            SELECT
                config_hash,
                protocol,
                alias,
                reliability_score,
                success_rate,
                avg_latency
            FROM scored_configs
            ORDER BY reliability_score DESC, total_attempts DESC
            LIMIT 1
        '''

        cursor = self._execute_with_retry(query, (network_id,))
        row = cursor.fetchone()

        if row:
            return BestConfig(
                config_hash=row[0],
                protocol=row[1],
                alias=row[2],
                reliability_score=row[3],
                success_rate=row[4],
                avg_latency=row[5] if row[5] is not None else 0.0
            )

        return None

    def list_network_ids(self) -> list[str]:
        """Return known network contexts stored in metrics."""
        cursor = self._execute_with_retry('SELECT DISTINCT network_id FROM metrics')
        return [row['network_id'] for row in cursor.fetchall()]

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
                    AVG(m.latency) as avg_latency
                FROM configs c
                JOIN metrics m ON c.id = m.config_id
                WHERE c.config_hash = ?
                  AND m.network_id = ?
            )
            SELECT
                total_attempts,
                success_count,
                avg_latency
            FROM config_stats
        '''
        cursor = self._execute_with_retry(query, (config_hash, network_id))
        row = cursor.fetchone()
        if row and row['total_attempts'] > 0:
            success_rate = row['success_count'] * 1.0 / row['total_attempts']
            latency_factor = 0.25 if row['avg_latency'] is None else (1.0 - min(1.0, max(0.0, (row['avg_latency'] - 20.0) / 480.0)))
            reliability_score = success_rate * 0.7 + latency_factor * 0.3
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
        cursor = self._execute_with_retry(query, (network_id,))
        return [row['port'] for row in cursor.fetchall() if row['port'] is not None]

    def get_config_by_hash(self, config_hash: str) -> Optional[ConfigEntry]:
        """Get config details by hash."""
        cursor = self._execute_with_retry(
            'SELECT * FROM configs WHERE config_hash = ?',
            (config_hash,)
        )
        row = cursor.fetchone()
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
        cursor = self._execute_with_retry(
            'SELECT mtu FROM network_mtu WHERE network_id = ?',
            (network_id,)
        )
        row = cursor.fetchone()
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
