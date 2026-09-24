"""Additive collection checkpoints and append-only parser/HTTP history."""
TABLES = ('pit_collections', 'pit_collection_pages', 'pit_parse_results', 'pit_http_attempts')


def create_schema(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS pit_collections (
      id TEXT PRIMARY KEY, provider TEXT NOT NULL, status TEXT NOT NULL
      CHECK(status IN ('planned','running','completed','failed','blocked')),
      document TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pit_collection_pages (
      collection_id TEXT NOT NULL REFERENCES pit_collections(id), page_key TEXT NOT NULL,
      raw_id TEXT NOT NULL REFERENCES pit_raw(id), document TEXT NOT NULL,
      PRIMARY KEY(collection_id,page_key));
    CREATE TABLE IF NOT EXISTS pit_parse_results (
      id TEXT PRIMARY KEY, raw_id TEXT NOT NULL REFERENCES pit_raw(id),
      parser_version TEXT NOT NULL, scope_key TEXT NOT NULL, status TEXT NOT NULL,
      document TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS pit_parse_lookup ON pit_parse_results(raw_id,parser_version,scope_key);
    CREATE TABLE IF NOT EXISTS pit_http_attempts (
      id INTEGER PRIMARY KEY, collection_id TEXT NOT NULL REFERENCES pit_collections(id),
      document TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS pit_raw_resource ON pit_raw(source_id,sha256,json_extract(document,'$.resource'));
    CREATE INDEX IF NOT EXISTS pit_event_type ON pit_events(data_type,source_id);
    ''')
    for table in TABLES[1:]:
        for action in ('UPDATE', 'DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Collection evidence is immutable'); END")
