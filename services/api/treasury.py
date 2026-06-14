import csv
import io

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from services.api_types import TreasuryHolder, TreasuryItem, TreasurySummary

router = APIRouter(prefix="/api/treasury", tags=["treasury"])

_SOURCE_LABEL = {"inventory": "隨身", "warehouse": "庫房"}
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(value: str) -> str:
    """Neutralize spreadsheet formula injection (CWE-1236) for free-text cells.

    Names can originate from scanned game memory (other players' shop/character
    names), so a value starting with =, +, -, @, tab or CR could be executed as
    a formula when the CSV is opened in Excel. Prefix such values with a quote.
    """
    if value and value[0] in _CSV_FORMULA_PREFIXES:
        return "'" + value
    return value


def _aggregate(rows: list[dict]) -> dict[int, dict]:
    """Group latest-snapshot rows by item_id; sum qty, merge holders by
    (character, source, account) so multiple inventory slots collapse into
    one entry per holder.
    """
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
                "_holders": {},
            }
            by_id[iid] = bucket
        qty = r["qty"]
        bucket["total_qty"] += qty
        if r["source"] == "warehouse":
            bucket["in_warehouse"] += qty
        else:
            bucket["on_person"] += qty
        key = (r["character"], r["source"], r.get("account"))
        bucket["_holders"][key] = bucket["_holders"].get(key, 0) + qty

    for bucket in by_id.values():
        merged = bucket.pop("_holders")
        bucket["holders"] = [
            TreasuryHolder(character=ch, source=src, account=acct, qty=q)
            for (ch, src, acct), q in merged.items()
        ]
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


@router.get("/export.csv")
async def treasury_export_csv(request: Request, mode: str = "summary") -> Response:
    """Treasury report as Excel-friendly CSV. mode=detail|summary (unknown → summary).

    Encoded UTF-8 with BOM (utf-8-sig) so Windows Excel (cp950 locale) opens
    Chinese names without mojibake. Reuses load_latest_snapshots + _aggregate;
    no new aggregation logic.
    """
    mode = "detail" if mode == "detail" else "summary"
    db = request.app.state.services.get("snapshot_db")
    if db is None:
        raise HTTPException(status_code=503, detail="snapshot database unavailable")
    rows = db.load_latest_snapshots()

    buf = io.StringIO()
    writer = csv.writer(buf)

    if mode == "detail":
        writer.writerow(["角色", "帳號", "來源", "道具ID", "道具名", "類型", "數量", "掃描時間"])
        for r in sorted(rows, key=lambda x: (x["character"], x["source"], x["item_id"])):
            writer.writerow(
                [
                    _safe_cell(r["character"]),
                    _safe_cell(r.get("account") or ""),
                    _SOURCE_LABEL.get(r["source"], "隨身"),
                    r["item_id"],
                    _safe_cell(r["name"]),
                    _safe_cell(r.get("item_type", "") or ""),
                    r["qty"],
                    r["scanned_at"],
                ]
            )
    else:
        writer.writerow(["道具ID", "道具名", "類型", "身上", "庫房", "合計", "持有角色數"])
        by_id = _aggregate(rows)
        for b in sorted(by_id.values(), key=lambda x: (-x["total_qty"], x["name"])):
            holder_chars = {h.character for h in b["holders"]}
            writer.writerow(
                [
                    b["item_id"],
                    _safe_cell(b["name"]),
                    _safe_cell(b["item_type"]),
                    b["on_person"],
                    b["in_warehouse"],
                    b["total_qty"],
                    len(holder_chars),
                ]
            )

    body = buf.getvalue().encode("utf-8-sig")
    filename = f"treasury-{mode}.csv"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
