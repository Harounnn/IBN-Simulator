from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

class SLA(BaseModel):
    latency_ms: int
    availability_pct: float
    min_bandwidth_mbps: int
    priority: str

class Selector(BaseModel):
    src: str
    dst: str
    app: str
    ports: str

class Intent(BaseModel):
    intent_id: Optional[str] = None
    name: str
    owner: str
    selectors: Selector
    sla: SLA
    description: Optional[str] = ""

class QosPolicy(BaseModel):
    class_name: str
    min_bandwidth_mbps: int

class RoutingHint(BaseModel):
    preferred_path: str
    avoid: Optional[str] = None

class Policy(BaseModel):
    intent_id: str
    qos: QosPolicy
    routing: RoutingHint
    acl: Dict[str, Any]
