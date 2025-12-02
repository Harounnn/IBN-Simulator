import time
from typing import Dict, Any

# Deterministic mock executor that applies a policy and returns success/failure

def apply_policy(policy: Dict[str, Any]) -> Dict[str, Any]:
    time.sleep(0.8)
    from random import random
    succeeded = random() > 0.05
    result = {"applied": succeeded, "policy": policy}
    return result