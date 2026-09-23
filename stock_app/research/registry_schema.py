"""Additive research-control schema. No changes to records or forecast authority."""


def create_schema(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS research_questions (
      id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
      parent_id TEXT REFERENCES research_questions(id), document TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS research_hypotheses (
      id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
      question_id TEXT NOT NULL REFERENCES research_questions(id), document TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS research_families (
      id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
      hypothesis_id TEXT NOT NULL REFERENCES research_hypotheses(id), document TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS research_specs (
      id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
      hypothesis_id TEXT REFERENCES research_hypotheses(id),
      family_id TEXT REFERENCES research_families(id),
      registration_mode TEXT NOT NULL CHECK(registration_mode IN ('preregistered','legacy_import')),
      primary_metric TEXT,
      document TEXT NOT NULL,
      CHECK(registration_mode='legacy_import' OR
        (hypothesis_id IS NOT NULL AND family_id IS NOT NULL AND length(primary_metric)>0)));
    CREATE TABLE IF NOT EXISTS research_trials (
      id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL,
      family_id TEXT NOT NULL REFERENCES research_families(id), document TEXT NOT NULL,
      UNIQUE(family_id,fingerprint));
    CREATE TABLE IF NOT EXISTS research_runs (
      id TEXT PRIMARY KEY, spec_id TEXT NOT NULL REFERENCES research_specs(id),
      execution_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
      origin TEXT NOT NULL, document TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS research_attempts (
      id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES research_runs(id),
      number INTEGER NOT NULL CHECK(number>0), created_at TEXT NOT NULL,
      document TEXT NOT NULL, UNIQUE(run_id,number), UNIQUE(id,run_id));
    CREATE TABLE IF NOT EXISTS research_outcomes (
      id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES research_runs(id),
      attempt_id TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('completed','failed','invalid','aborted','blocked')),
      disposition TEXT NOT NULL CHECK(disposition IN
        ('supported','not_supported','inconclusive','invalid','failed','aborted','blocked')),
      primary_metric TEXT, document TEXT NOT NULL,
      FOREIGN KEY(attempt_id,run_id) REFERENCES research_attempts(id,run_id));
    CREATE TABLE IF NOT EXISTS research_events (
      id TEXT PRIMARY KEY, created_at TEXT NOT NULL, run_id TEXT REFERENCES research_runs(id),
      attempt_id TEXT REFERENCES research_attempts(id), trial_id TEXT REFERENCES research_trials(id),
      kind TEXT NOT NULL, document TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS research_artifacts (
      id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES research_runs(id),
      attempt_id TEXT NOT NULL, role TEXT NOT NULL, sha256 TEXT NOT NULL,
      path TEXT NOT NULL, created_at TEXT NOT NULL,
      FOREIGN KEY(attempt_id,run_id) REFERENCES research_attempts(id,run_id),
      UNIQUE(run_id,attempt_id,role,sha256));
    CREATE TABLE IF NOT EXISTS research_model_links (
      model_id TEXT NOT NULL, model_kind TEXT NOT NULL DEFAULT 'models' CHECK(model_kind='models'),
      run_id TEXT NOT NULL REFERENCES research_runs(id), artifact_id TEXT NOT NULL REFERENCES research_artifacts(id),
      created_at TEXT NOT NULL, PRIMARY KEY(model_id,run_id),
      FOREIGN KEY(model_kind,model_id) REFERENCES records(kind,id));
    CREATE INDEX IF NOT EXISTS research_events_run ON research_events(run_id,created_at);
    CREATE INDEX IF NOT EXISTS research_attempts_run ON research_attempts(run_id,number);
    CREATE TRIGGER IF NOT EXISTS research_attempt_transition BEFORE INSERT ON research_attempts
    BEGIN
      SELECT CASE WHEN EXISTS(SELECT 1 FROM research_outcomes WHERE run_id=NEW.run_id AND state='completed')
        THEN RAISE(ABORT,'completed run cannot restart') END;
      SELECT CASE WHEN EXISTS(SELECT 1 FROM research_attempts a WHERE a.run_id=NEW.run_id
        AND NOT EXISTS(SELECT 1 FROM research_outcomes o WHERE o.attempt_id=a.id))
        THEN RAISE(ABORT,'unfinished attempt requires reconciliation') END;
      SELECT CASE WHEN NEW.number != (SELECT count(*)+1 FROM research_attempts WHERE run_id=NEW.run_id)
        THEN RAISE(ABORT,'attempt sequence violation') END;
    END;
    CREATE TRIGGER IF NOT EXISTS research_spec_contract BEFORE INSERT ON research_specs
    WHEN NEW.registration_mode='preregistered'
    BEGIN
      SELECT CASE WHEN json_extract(NEW.document,'$.contract.primary_metric') IS NOT NEW.primary_metric
        THEN RAISE(ABORT,'spec metric column disagrees with document') END;
      SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM research_families f
        WHERE f.id=NEW.family_id AND f.hypothesis_id=NEW.hypothesis_id)
        THEN RAISE(ABORT,'spec family and hypothesis mismatch') END;
    END;
    CREATE TRIGGER IF NOT EXISTS research_event_scope BEFORE INSERT ON research_events
    BEGIN
      SELECT CASE WHEN NEW.attempt_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM research_attempts a
        WHERE a.id=NEW.attempt_id AND a.run_id=NEW.run_id)
        THEN RAISE(ABORT,'event attempt scope mismatch') END;
      SELECT CASE WHEN NEW.trial_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM research_trials t
        JOIN research_specs s ON s.family_id=t.family_id JOIN research_runs r ON r.spec_id=s.id
        WHERE t.id=NEW.trial_id AND r.id=NEW.run_id)
        THEN RAISE(ABORT,'event trial scope mismatch') END;
    END;
    CREATE TRIGGER IF NOT EXISTS research_metric_contract BEFORE INSERT ON research_outcomes
    BEGIN
      SELECT CASE WHEN NEW.primary_metric IS NOT
        (SELECT s.primary_metric FROM research_specs s JOIN research_runs r ON r.spec_id=s.id WHERE r.id=NEW.run_id)
        THEN RAISE(ABORT,'declared primary metric cannot change') END;
      SELECT CASE WHEN (NEW.state='completed' AND NEW.disposition NOT IN ('supported','not_supported','inconclusive'))
         OR (NEW.state!='completed' AND NEW.disposition!=NEW.state)
        THEN RAISE(ABORT,'state and disposition disagree') END;
    END;
    CREATE TRIGGER IF NOT EXISTS research_link_scope BEFORE INSERT ON research_model_links
    BEGIN
      SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM research_artifacts a
        WHERE a.id=NEW.artifact_id AND a.run_id=NEW.run_id AND a.role='candidate')
        THEN RAISE(ABORT,'candidate link scope mismatch') END;
    END;
    ''')
    for table in ('questions', 'hypotheses', 'families', 'specs', 'trials', 'runs',
                  'attempts', 'outcomes', 'events', 'artifacts', 'model_links'):
        for action in ('UPDATE', 'DELETE'):
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS research_{table}_{action.lower()}
                BEFORE {action} ON research_{table} BEGIN
                SELECT RAISE(ABORT,'research history is immutable'); END''')
