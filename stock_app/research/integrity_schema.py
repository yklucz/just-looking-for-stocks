"""Additive append-only verification and operator-review history."""

TABLES = ('integrity_reproductions', 'integrity_results', 'integrity_cases', 'integrity_actions')


def create_schema(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS integrity_reproductions (
      id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES research_runs(id),
      request_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, document TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS integrity_results (
      reproduction_id TEXT PRIMARY KEY REFERENCES integrity_reproductions(id),
      created_at TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('exact','equivalent','different','unavailable','failed','blocked')),
      document TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS integrity_cases (
      id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, category TEXT NOT NULL,
      source TEXT NOT NULL, severity TEXT NOT NULL CHECK(severity IN ('warning','error')),
      created_at TEXT NOT NULL, document TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS integrity_actions (
      id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES integrity_cases(id),
      sequence INTEGER NOT NULL CHECK(sequence>0), action TEXT NOT NULL,
      actor TEXT NOT NULL CHECK(length(trim(actor))>=8), reason TEXT NOT NULL CHECK(length(trim(reason))>=8),
      created_at TEXT NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('under_review','resolved','dismissed')),
      document TEXT NOT NULL, UNIQUE(case_id,sequence));
    CREATE INDEX IF NOT EXISTS integrity_reproduction_run ON integrity_reproductions(run_id,created_at);
    CREATE INDEX IF NOT EXISTS integrity_case_category ON integrity_cases(category,created_at);
    CREATE TRIGGER IF NOT EXISTS integrity_action_transition BEFORE INSERT ON integrity_actions
    BEGIN
      SELECT CASE WHEN NEW.sequence != (SELECT count(*)+1 FROM integrity_actions WHERE case_id=NEW.case_id)
        THEN RAISE(ABORT,'action sequence violation') END;
      SELECT CASE WHEN EXISTS(SELECT 1 FROM integrity_actions WHERE case_id=NEW.case_id AND state IN ('resolved','dismissed'))
        THEN RAISE(ABORT,'case is terminal') END;
      SELECT CASE WHEN NEW.action NOT IN ('begin_review','manual_evidence_added','dismissed_false_positive',
        'confirmed_existing_artifact','linked_existing_candidate','run_marked_blocked','retry_authorized','operational_cancelled','confirmed_operational_resolution')
        THEN RAISE(ABORT,'unknown reconciliation action') END;
      SELECT CASE WHEN (NEW.action IN ('begin_review','manual_evidence_added','run_marked_blocked') AND NEW.state!='under_review')
        OR (NEW.action='dismissed_false_positive' AND NEW.state!='dismissed')
        OR (NEW.action NOT IN ('begin_review','manual_evidence_added','run_marked_blocked','dismissed_false_positive') AND NEW.state!='resolved')
        THEN RAISE(ABORT,'action state mismatch') END;
    END;
    ''')
    for table in TABLES:
        for action in ('UPDATE', 'DELETE'):
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()} BEFORE {action} ON {table}
              BEGIN SELECT RAISE(ABORT,'integrity history is immutable'); END''')
