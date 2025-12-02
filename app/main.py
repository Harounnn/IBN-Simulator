"""
FastAPI entrypoint.

Endpoints:
- POST /intents         -> create an intent 
- GET  /intents/{id}    -> fetch intent record + audit + attached policy
- GET  /telemetry/{id}  -> get last simulated telemetry for intent 
"""
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
from typing import Dict, Any
import uuid
import logging

from .store import save_intent, get_intent, update_status, attach_policy, append_audit
from .llm_translator import llm_translate_intent
from .executor import apply_policy
from .assurance import start_background_loop, telemetry_state

logger = logging.getLogger("ibn.main")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(handler)

app = FastAPI(title="IBN Gemini POC", version="0.1")

@app.on_event("startup")
def startup_event():
    start_background_loop()
    logger.info("IBN POC started, assurance loop launched.")

class CreateIntent(BaseModel):
    name: str
    owner: str
    selectors: Dict[str, Any]
    sla: Dict[str, Any]
    description: str = ""

@app.post("/intents", status_code=status.HTTP_201_CREATED)
def create_intent(payload: CreateIntent):
    intent_id = str(uuid.uuid4())
    intent = {
        "intent_id": intent_id,
        "name": payload.name,
        "owner": payload.owner,
        "selectors": payload.selectors,
        "sla": payload.sla,
        "description": payload.description,
    }

    try:
        save_intent(intent, status="submitted")
    except Exception as e:
        logger.exception("Failed to save intent")
        raise HTTPException(status_code=500, detail=f"failed to save intent: {e}")

    try:
        policy = llm_translate_intent(intent)
    except Exception as e:
        append_audit(intent_id, f"Translation failed: {e}")
        update_status(intent_id, "error")
        logger.exception("LLM translation failed for %s", intent_id)
        raise HTTPException(status_code=500, detail="intent translation failed")

    try:
        attach_policy(intent_id, policy.dict())
        update_status(intent_id, "deploying")
    except Exception as e:
        append_audit(intent_id, f"Attach policy failed: {e}")
        update_status(intent_id, "error")
        logger.exception("Failed to attach policy for %s", intent_id)
        raise HTTPException(status_code=500, detail="failed to attach policy")

    try:
        res = apply_policy(policy.dict())
        if res.get("applied"):
            update_status(intent_id, "deployed")
            append_audit(intent_id, "Policy applied successfully")
            return {"intent_id": intent_id, "status": "deployed"}
        else:
            update_status(intent_id, "error")
            append_audit(intent_id, "Policy application failed")
            raise HTTPException(status_code=500, detail="policy application failed")
    except HTTPException:
        raise
    except Exception as e:
        update_status(intent_id, "error")
        append_audit(intent_id, f"Policy application exception: {e}")
        logger.exception("Error applying policy for %s", intent_id)
        raise HTTPException(status_code=500, detail="policy application error")

@app.get("/intents/{intent_id}")
def get_intent_endpoint(intent_id: str):
    data = get_intent(intent_id)
    if not data:
        raise HTTPException(status_code=404, detail="intent not found")
    return data

@app.get("/telemetry/{intent_id}")
def get_telemetry(intent_id: str):
    return telemetry_state.get(intent_id, {})

@app.get("/healthz")
def health():
    return {"status": "ok"}
