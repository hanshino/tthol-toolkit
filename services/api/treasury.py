
from fastapi import APIRouter, Request

from services.api_types import TreasuryHolder, TreasuryItem, TreasurySummary

router = APIRouter(prefix="/api/treasury", tags=["treasury"])


def _aggregate(rows: list[dict]) -> dict[int, dict]:
    """Group latest-snapshot rows by item_id; sum qty, collect holders."""
    by_id: dict[int, dict] = {}
    for r in rows:
        iid = r["item_id"]
        bucket = by_id.get(iid)
        if bucket is None:
            bucket = {
                "item_id": iid,
                "name": r["name"],
                "item_type": r.get("item_type", "") or "",
                "total_qty": 0,
                "on_person": 0,
                "in_warehouse": 0,
                "holders": [],
            }
            by_id[iid] = bucket
        qty = r["qty"]
        bucket["total_qty"] += qty
        if r["source"] == "warehouse":
            bucket["in_warehouse"] += qty
        else:
            bucket["on_person"] += qty
        bucket["holders"].append(
            TreasuryHolder(
                character=r["character"],
                source=r["source"],
                account=r.get("account"),
                qty=qty,
            )
        )
    return by_id


@router.get("/summary", response_model=TreasurySummary)
async def treasury_summary(request: Request) -> TreasurySummary:
    db = request.app.state.services.get("snapshot_db")
    if db is None:
        return TreasurySummary(total_kinds=0, total_qty=0, on_person=0, in_warehouse=0)
    rows = db.load_latest_snapshots()
    by_id = _aggregate(rows)
    total_qty = sum(b["total_qty"] for b in by_id.values())
    on_person = sum(b["on_person"] for b in by_id.values())
    in_warehouse = sum(b["in_warehouse"] for b in by_id.values())
    return TreasurySummary(
        total_kinds=len(by_id),
        total_qty=total_qty,
        on_person=on_person,
        in_warehouse=in_warehouse,
    )


@router.get("/items", response_model=list[TreasuryItem])
async def treasury_items(request: Request, search: str | None = None) -> list[TreasuryItem]:
    db = request.app.state.services.get("snapshot_db")
    if db is None:
        return []
    rows = db.load_latest_snapshots()
    by_id = _aggregate(rows)
    items: list[TreasuryItem] = []
    needle = (search or "").strip().lower()
    for b in by_id.values():
        if needle:
            hay = f"{b['name']} {b['item_type']}".lower()
            if needle not in hay:
                continue
        items.append(
            TreasuryItem(
                item_id=b["item_id"],
                name=b["name"],
                item_type=b["item_type"],
                total_qty=b["total_qty"],
                on_person=b["on_person"],
                in_warehouse=b["in_warehouse"],
                holders=b["holders"],
            )
        )
    items.sort(key=lambda i: (-i.total_qty, i.name))
    return items
