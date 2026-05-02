"""Write the FastAPI OpenAPI schema to webui/openapi.json without booting uvicorn.

Run from repo root:

    uv run python scripts/gen_openapi.py
    cd webui && npm run gen-types

Regenerates webui/src/api/schema.ts (the openapi-typescript output). The
hand-written webui/src/api/types.ts is a thin facade over schema.ts and
should NOT be regenerated.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from services.api import build_app  # noqa: E402  (needs sys.path tweak above)


def main() -> int:
    schema = build_app().openapi()
    out = REPO / "webui" / "openapi.json"
    out.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(schema['paths'])} paths to {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
