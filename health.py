import json
from flowsteward_extension_sdk import HOST_CONTRACT_VERSION

print(json.dumps({"ok": True, "status": "healthy", "contract_version": HOST_CONTRACT_VERSION}))
