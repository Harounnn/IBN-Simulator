import os
import json
import re
import logging
from typing import Dict, Any, List, Optional

from pydantic import ValidationError

from .schemas import Intent, Policy  

logger = logging.getLogger("ibn.llm_translator")
logger.setLevel(logging.INFO)

try:
    import google.generativeai as genai  
    GENAI_MODULE = True
except Exception:
    genai = None  
    GENAI_MODULE = False

GENAI_API_KEY = os.getenv("GENAI_API_KEY") or os.getenv("GENAI_KEY") or None
if GENAI_MODULE and GENAI_API_KEY:
    try:
        genai.configure(api_key=GENAI_API_KEY)
    except Exception as e:
        logger.warning("Failed to configure genai with API key: %s", e)

def _extract_first_json(text: str) -> str:
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model response")
    stack = []
    end = start
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            stack.append("{")
        elif ch == "}":
            if not stack:
                raise ValueError("Mismatched closing brace")
            stack.pop()
            if not stack:
                end = i
                break
    return text[start:end + 1]

NL_PARSE_INSTRUCTIONS = """
You are a strict JSON extractor for network intents.
Input: free-form English describing a network intent.
Output: ONLY a JSON object (no explanation) matching exactly this schema:

{
  "name": "short name for the intent",
  "owner": "email or team owning the intent",
  "selectors": {
    "src": "source CIDR or name",
    "dst": "destination CIDR or name",
    "app": "application name",
    "ports": "port or port-range or protocol"
  },
  "sla": {
    "latency_ms": integer,
    "availability_pct": float,
    "min_bandwidth_mbps": integer,
    "priority": "low|medium|high"
  },
  "description": "optional short text (can be empty)"
}

Please ensure:
 - Numbers are numbers (not strings).
 - Fields exist even if empty (provide reasonable defaults).
 - Return compact JSON only (no surrounding text).
"""

POLICY_SCHEMA_PROMPT = """
Produce a JSON object matching this schema:

{
  "intent_id": "string",
  "qos": {"class_name":"string", "min_bandwidth_mbps": integer},
  "routing": {"preferred_path":"string", "avoid":"string or null"},
  "acl": {"allow": ["port_or_protocol_strings"]}
}

Return JSON only.
"""


def parse_intent_from_text(text: str, context: Optional[List[Dict[str,str]]] = None) -> Dict[str, Any]:

    if not GENAI_MODULE or not (GENAI_API_KEY or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")):
        logger.info("GENAI not configured - using deterministic NL fallback parser")
        name = "NL-Intent"
        owner = "unknown@example.com"
        selectors = {"src": "", "dst": "", "app": "", "ports": ""}
        sla = {"latency_ms": 50, "availability_pct": 99.9, "min_bandwidth_mbps": 100, "priority": "medium"}
        description = text

        m_src = re.search(r"src(?:ed|:)?\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\/\d+)", text)
        m_dst = re.search(r"dst(?:ination|:)?\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\/\d+)", text)
        m_src2 = re.search(r"from\s+\(?([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\/\d+)\)?", text)
        m_dst2 = re.search(r"to\s+\(?([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\/\d+)\)?", text)
        if m_src:
            selectors["src"] = m_src.group(1)
        elif m_src2:
            selectors["src"] = m_src2.group(1)
        if m_dst:
            selectors["dst"] = m_dst.group(1)
        elif m_dst2:
            selectors["dst"] = m_dst2.group(1)

        m_port = re.search(r"port[s]?\s*(?:is|=|:)?\s*([0-9]{1,5})", text)
        if m_port:
            selectors["ports"] = m_port.group(1)
        m_app = re.search(r"app(?:lication)?\s*(?:named)?\s*([A-Za-z0-9_\-]+)", text)
        if m_app:
            selectors["app"] = m_app.group(1)

        m_latency = re.search(r"latency\s*(?:under|<|less than)?\s*([0-9]{1,4})\s*ms", text)
        if m_latency:
            sla["latency_ms"] = int(m_latency.group(1))
        m_bw = re.search(r"([0-9]{2,4})\s*Mbps", text)
        if m_bw:
            sla["min_bandwidth_mbps"] = int(m_bw.group(1))
        m_priority = re.search(r"\b(high|medium|low)\b", text, re.IGNORECASE)
        if m_priority:
            sla["priority"] = m_priority.group(1).lower()
        m_owner = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", text)
        if m_owner:
            owner = m_owner.group(0)

        candidate = {
            "name": name,
            "owner": owner,
            "selectors": selectors,
            "sla": sla,
            "description": description
        }
        intent = Intent(**candidate)
        return intent.dict()

    logger.info("Calling Gemini to parse NL intent")
    prompt = NL_PARSE_INSTRUCTIONS + "\n\nInput:\n" + text
    if context:
        prompt += "\n\nContext:\n" + json.dumps(context)

    try:
        resp = genai.generate_text(model="gemini", prompt=prompt, max_output_tokens=512, temperature=0.0)
        raw = resp.text
    except Exception as e:
        logger.exception("GenAI call failed: %s", e)
        raise RuntimeError(f"GenAI call failed: {e}")

    try:
        js = _extract_first_json(raw)
        parsed = json.loads(js)
    except Exception as e:
        logger.exception("Failed to parse JSON from Gemini: %s -- raw: %s", e, raw)
        raise ValueError(f"Failed to parse JSON from model output: {e}. Raw output: {raw}")

    try:
        intent_obj = Intent(**parsed)
    except ValidationError as e:
        logger.exception("Parsed intent failed validation: %s", e)
        raise

    return intent_obj.dict()

def llm_translate_intent(intent: Dict[str, Any], context: Optional[List[Dict[str,str]]] = None) -> Policy:
    if not GENAI_MODULE or not (GENAI_API_KEY or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")):
        logger.info("GENAI not configured - using deterministic translator fallback")
        policy = {
            "intent_id": intent["intent_id"],
            "qos": {
                "class_name": "premium" if str(intent["sla"].get("priority","")).lower() == "high" else "standard",
                "min_bandwidth_mbps": int(intent["sla"].get("min_bandwidth_mbps", 100))
            },
            "routing": {
                "preferred_path": "low-latency" if int(intent["sla"].get("latency_ms", 999)) <= 50 else "cost-optimized",
                "avoid": intent.get("constraints", {}).get("avoid_country")
            },
            "acl": {"allow": [intent["selectors"].get("ports","")]}
        }
        return Policy(**policy)

    logger.info("Calling Gemini to translate intent to policy")
    prompt_parts = [POLICY_SCHEMA_PROMPT, "Intent:\n" + json.dumps(intent, default=str)]
    if context:
        prompt_parts.append("Context:\n" + json.dumps(context, default=str))
    prompt = "\n\n".join(prompt_parts)

    try:
        resp = genai.generate_text(model="gemini", prompt=prompt, max_output_tokens=512, temperature=0.0)
        raw = resp.text
    except Exception as e:
        logger.exception("GenAI translation failed: %s", e)
        raise RuntimeError(f"GenAI translation failed: {e}")

    try:
        js = _extract_first_json(raw)
        parsed = json.loads(js)
    except Exception as e:
        logger.exception("Failed to extract JSON from policy output: %s -- raw: %s", e, raw)
        raise ValueError(f"Failed to parse JSON from model output: {e}. Raw: {raw}")

    try:
        policy = Policy(**parsed)
    except ValidationError as e:
        logger.exception("Policy validation failed: %s", e)
        raise

    return policy
