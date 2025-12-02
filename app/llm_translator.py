import os
import json
from typing import Dict, Any, List
from pydantic import ValidationError
from .schemas import Policy

try:
    import google.generativeai as genai
    GENAI_AVAILABLE = True
except Exception:
    GENAI_AVAILABLE = False

GENAI_API_KEY = os.getenv('GENAI_API_KEY') or os.getenv('GENAI_KEY') or None

if GENAI_AVAILABLE and GENAI_API_KEY:
    genai.configure(api_key=GENAI_API_KEY)

POLICY_SCHEMA_PROMPT = '''
You are a network intent translator. Output ONLY a single JSON object that matches this schema:
{
  "intent_id": "string",
  "qos": {"class_name":"string", "min_bandwidth_mbps": integer},
  "routing": {"preferred_path":"string", "avoid": "string or null"},
  "acl": {"allow": ["port_or_protocol_strings"]}
}
Make sure integer fields are numbers and not strings. Avoid any extra top-level fields. Return minimal explanations only if asked separately.
'''


def llm_translate_intent(intent: Dict[str, Any], context: List[Dict[str,str]] = None) -> Policy:
    """Translate an intent into a Policy. If Gemini (genai) is configured, call it; otherwise use deterministic mock.
    Returns a validated Policy object.
    """
    if not GENAI_AVAILABLE or not GENAI_API_KEY:
        policy = {
            "intent_id": intent['intent_id'],
            "qos": {"class_name": "premium" if str(intent['sla'].get('priority','')).lower() == 'high' else 'standard',
                    "min_bandwidth_mbps": int(intent['sla']['min_bandwidth_mbps'])},
            "routing": {"preferred_path": "low-latency" if int(intent['sla'].get('latency_ms',999)) <= 50 else 'cost-optimized',
                        "avoid": intent.get('constraints', {}).get('avoid_country')},
            "acl": {"allow": [intent['selectors']['ports']]}
        }
        return Policy(**policy)

    prompt_parts = [POLICY_SCHEMA_PROMPT, f"Intent: {json.dumps(intent)}"]
    if context:
        prompt_parts.append("Context:" + json.dumps(context))
    prompt = "".join(prompt_parts)

    try:
        response = genai.generate_text(model='gemini',  # generic; pick appropriate model variant in prod
                                       prompt=prompt,
                                       max_output_tokens=800,
                                       temperature=0.0)
        text = response.text
    except Exception as e:
        raise RuntimeError(f"GenAI call failed: {e}")

    try:
        start = text.find('{')
        end = text.rfind('}')
        json_text = text[start:end+1]
        args = json.loads(json_text)
    except Exception as e:
        raise RuntimeError(f"Failed to parse JSON from model output: {e} -- raw: {text}")

    try:
        policy = Policy(**args)
    except ValidationError as e:
        raise
    return policy