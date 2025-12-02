import sqlite3
from typing import Dict, Any
from threading import Lock
import json

_db_lock = Lock()
conn = sqlite3.connect('intents.db', check_same_thread=False)
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS intents (
    intent_id TEXT PRIMARY KEY,
    name TEXT,
    owner TEXT,
    selectors TEXT,
    sla TEXT,
    description TEXT,
    status TEXT,
    policy TEXT,
    audit_log TEXT
)
""")
conn.commit()


def save_intent(intent: Dict[str, Any], status: str = 'submitted'):
    with _db_lock:
        cur.execute(
            "INSERT INTO intents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                intent['intent_id'],
                intent['name'],
                intent['owner'],
                json.dumps(intent['selectors']),
                json.dumps(intent['sla']),
                intent.get('description',''),
                status,
                None,
                json.dumps([]),
            ),
        )
        conn.commit()


def update_status(intent_id: str, status: str):
    with _db_lock:
        cur.execute("UPDATE intents SET status=? WHERE intent_id=?", (status, intent_id))
        conn.commit()


def attach_policy(intent_id: str, policy: Dict[str, Any]):
    with _db_lock:
        cur.execute("UPDATE intents SET policy=? WHERE intent_id=?", (json.dumps(policy), intent_id))
        conn.commit()


def append_audit(intent_id: str, msg: str):
    with _db_lock:
        cur.execute("SELECT audit_log FROM intents WHERE intent_id=?", (intent_id,))
        row = cur.fetchone()
        if not row:
            return
        log = json.loads(row[0] or "[]")
        log.append(msg)
        cur.execute("UPDATE intents SET audit_log=? WHERE intent_id=?", (json.dumps(log), intent_id))
        conn.commit()


def get_intent(intent_id: str):
    with _db_lock:
        cur.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,))
        row = cur.fetchone()
        if not row:
            return None
        keys = ['intent_id','name','owner','selectors','sla','description','status','policy','audit_log']
        r = dict(zip(keys, row))
        r['selectors'] = json.loads(r['selectors'])
        r['sla'] = json.loads(r['sla'])
        r['policy'] = json.loads(r['policy']) if r['policy'] else None
        r['audit_log'] = json.loads(r['audit_log'] or '[]')
        return r