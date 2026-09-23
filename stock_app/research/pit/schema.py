"""Additive immutable PIT schema; no historical conversion or provider seeding."""
TABLES = ('pit_sources','pit_identities','pit_identifiers','pit_raw','pit_events','pit_revisions')


def create_schema(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS pit_sources (
      id TEXT PRIMARY KEY, provider TEXT NOT NULL, feed TEXT NOT NULL,
      fingerprint TEXT NOT NULL, created_at TEXT NOT NULL, document TEXT NOT NULL,
      UNIQUE(provider,feed));
    CREATE TABLE IF NOT EXISTS pit_identities (
      id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('entity','security','listing')),
      parent_id TEXT REFERENCES pit_identities(id), fingerprint TEXT NOT NULL,
      created_at TEXT NOT NULL, document TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pit_raw (
      id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES pit_sources(id),
      sha256 TEXT NOT NULL, observed_time TEXT NOT NULL, ingested_time TEXT NOT NULL,
      fingerprint TEXT NOT NULL UNIQUE, document TEXT NOT NULL, UNIQUE(id,source_id),
      CHECK(observed_time<=ingested_time));
    CREATE TABLE IF NOT EXISTS pit_identifiers (
      id TEXT PRIMARY KEY, identity_id TEXT NOT NULL REFERENCES pit_identities(id),
      source_id TEXT NOT NULL REFERENCES pit_sources(id), namespace TEXT NOT NULL,
      value TEXT NOT NULL, scope TEXT NOT NULL, valid_from TEXT, valid_to TEXT,
      raw_id TEXT REFERENCES pit_raw(id), fingerprint TEXT NOT NULL UNIQUE,
      created_at TEXT NOT NULL, document TEXT NOT NULL,
      CHECK(valid_to IS NULL OR valid_from IS NULL OR valid_from<valid_to));
    CREATE INDEX IF NOT EXISTS pit_identifier_lookup ON pit_identifiers(namespace,value,scope,valid_from,valid_to);
    CREATE TABLE IF NOT EXISTS pit_events (
      id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES pit_sources(id),
      source_key TEXT NOT NULL, data_type TEXT NOT NULL,
      identity_id TEXT REFERENCES pit_identities(id), fingerprint TEXT NOT NULL,
      created_at TEXT NOT NULL, document TEXT NOT NULL, UNIQUE(source_id,data_type,source_key), UNIQUE(id,source_id));
    CREATE TABLE IF NOT EXISTS pit_revisions (
      id TEXT PRIMARY KEY, event_id TEXT NOT NULL, source_id TEXT NOT NULL,
      raw_id TEXT NOT NULL, source_record_id TEXT NOT NULL,
      supersedes TEXT REFERENCES pit_revisions(id), fingerprint TEXT NOT NULL UNIQUE,
      availability_kind TEXT NOT NULL CHECK(availability_kind IN ('exact','date_level','proxy','observed_only','unknown')),
      available_bound TEXT, observed_time TEXT NOT NULL, ingested_time TEXT NOT NULL,
      document TEXT NOT NULL,
      FOREIGN KEY(event_id,source_id) REFERENCES pit_events(id,source_id),
      FOREIGN KEY(raw_id,source_id) REFERENCES pit_raw(id,source_id),
      CHECK(observed_time<=ingested_time),
      CHECK(availability_kind NOT IN ('exact','proxy','date_level','observed_only') OR available_bound IS NOT NULL));
    CREATE INDEX IF NOT EXISTS pit_revision_asof ON pit_revisions(event_id,available_bound,observed_time);
    CREATE INDEX IF NOT EXISTS pit_event_query ON pit_events(identity_id,data_type,source_id);
    CREATE TRIGGER IF NOT EXISTS pit_identity_parent BEFORE INSERT ON pit_identities
    BEGIN
      SELECT CASE WHEN (NEW.kind='entity' AND NEW.parent_id IS NOT NULL)
        OR (NEW.kind='security' AND NOT EXISTS(SELECT 1 FROM pit_identities WHERE id=NEW.parent_id AND kind='entity'))
        OR (NEW.kind='listing' AND NOT EXISTS(SELECT 1 FROM pit_identities WHERE id=NEW.parent_id AND kind='security'))
        THEN RAISE(ABORT,'identity parent kind mismatch') END;
    END;
    CREATE TRIGGER IF NOT EXISTS pit_revision_lineage BEFORE INSERT ON pit_revisions
    WHEN NEW.supersedes IS NOT NULL
    BEGIN
      SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM pit_revisions WHERE id=NEW.supersedes AND event_id=NEW.event_id
        AND observed_time<=NEW.observed_time AND (available_bound IS NULL OR NEW.available_bound IS NULL OR available_bound<=NEW.available_bound))
        THEN RAISE(ABORT,'conflicting revision lineage') END;
    END;
    CREATE TRIGGER IF NOT EXISTS pit_raw_source BEFORE INSERT ON pit_identifiers WHEN NEW.raw_id IS NOT NULL
    BEGIN
      SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM pit_raw WHERE id=NEW.raw_id AND source_id=NEW.source_id)
        THEN RAISE(ABORT,'identifier evidence source mismatch') END;
    END;
    CREATE TRIGGER IF NOT EXISTS pit_cik_entity BEFORE INSERT ON pit_identifiers WHEN NEW.namespace='cik'
    BEGIN
      SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM pit_identities WHERE id=NEW.identity_id AND kind='entity')
        THEN RAISE(ABORT,'CIK identifies entity, not security') END;
    END;
    ''')
    for table in TABLES:
        for action in ('UPDATE','DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'PIT history is immutable'); END")
