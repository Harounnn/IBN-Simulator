import threading
import time
import random
import logging
from typing import Dict, Any, Optional

from .store import get_intent, update_status, append_audit, attach_policy, conn
from .llm_translator import llm_translate_intent
from .executor import apply_policy

logger = logging.getLogger("ibn.assurance")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(handler)

telemetry_state: Dict[str, Dict[str, float]] = {}

# configuration
TELEMETRY_POLL_SECONDS = 5
LLM_RETRY_ATTEMPTS = 2
LLM_RETRY_BACKOFF = 1.5 

def _list_intents_from_db():
    """Return list of (intent_id, status). Use a fresh cursor for thread-safety."""
    try:
        cur = conn.cursor()
        cur.execute("SELECT intent_id, status FROM intents")
        rows = cur.fetchall()
        cur.close()
        return rows
    except Exception as e:
        logger.exception("Failed to list intents from DB: %s", e)
        return []

def _simulate_metrics_for_intent(intent_id: str) -> Dict[str, float]:
    latency = random.uniform(20, 120)          
    availability = random.uniform(98.0, 100.0) 
    bandwidth = random.uniform(50, 400)       
    return {"latency": latency, "availability": availability, "bandwidth": bandwidth}

def _should_remediate(sla: Dict[str, Any], metrics: Dict[str, float]) -> bool:
    return (
        metrics["latency"] > sla.get("latency_ms", float("inf"))
        or metrics["availability"] < sla.get("availability_pct", 0.0)
        or metrics["bandwidth"] < sla.get("min_bandwidth_mbps", 0)
    )

def _call_llm_with_retries(payload: Dict[str, Any], context: Optional[list] = None):
    attempt = 0
    backoff = 1.0
    while attempt <= LLM_RETRY_ATTEMPTS:
        try:
            return llm_translate_intent(payload, context=context)
        except Exception as e:
            attempt += 1
            logger.warning("LLM translate failed (attempt %d/%d): %s", attempt, LLM_RETRY_ATTEMPTS + 1, e)
            if attempt > LLM_RETRY_ATTEMPTS:
                logger.error("LLM translate: out of retries")
                raise
            time.sleep(backoff)
            backoff *= LLM_RETRY_BACKOFF

def telemetry_loop():
    logger.info("Assurance telemetry loop starting (poll interval %s seconds)", TELEMETRY_POLL_SECONDS)
    while True:
        rows = _list_intents_from_db()
        for row in rows:
            try:
                intent_id, status = row
            except Exception:
                continue

            if status not in ("deployed", "assured"):
                continue

            metrics = _simulate_metrics_for_intent(intent_id)
            telemetry_state[intent_id] = metrics

            intent = get_intent(intent_id)
            if not intent:
                logger.debug("Intent %s disappeared from DB, skipping", intent_id)
                continue

            sla = intent.get("sla", {})
            if _should_remediate(sla, metrics):
                msg = f"SLA breach: latency={metrics['latency']:.1f}ms avail={metrics['availability']:.2f}% bw={metrics['bandwidth']:.1f}Mbps"
                append_audit(intent_id, msg)
                logger.info("[%s] %s", intent_id, msg)

                payload = {
                    "intent_id": intent_id,
                    "selectors": intent.get("selectors", {}),
                    "sla": sla,
                    "last_metrics": metrics
                }

                try:
                    new_policy = _call_llm_with_retries(payload, context=None)
                except Exception as e:
                    append_audit(intent_id, f"Remediation failed: LLM unavailable or error: {e}")
                    logger.exception("LLM remediation failed for %s", intent_id)
                    continue

                try:
                    attach_policy(intent_id, new_policy.dict())
                    append_audit(intent_id, "LLM produced remediation policy and attached")
                    logger.info("[%s] LLM remediation produced policy", intent_id)
                except Exception as e:
                    append_audit(intent_id, f"Remediation failed: cannot attach policy: {e}")
                    logger.exception("Failed to attach policy for %s", intent_id)
                    continue

                try:
                    res = apply_policy(new_policy.dict())
                    if res.get("applied"):
                        update_status(intent_id, "deployed")
                        append_audit(intent_id, "Remediation applied successfully")
                        logger.info("[%s] Remediation applied successfully", intent_id)
                    else:
                        append_audit(intent_id, "Remediation application failed")
                        logger.warning("[%s] Remediation application failed", intent_id)
                except Exception as e:
                    append_audit(intent_id, f"Remediation application error: {e}")
                    logger.exception("Error applying remediation for %s", intent_id)
            else:
                update_status(intent_id, "assured")
        time.sleep(TELEMETRY_POLL_SECONDS)

def start_background_loop():
    t = threading.Thread(target=telemetry_loop, daemon=True)
    t.start()
    return t
