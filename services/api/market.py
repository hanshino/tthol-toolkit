"""Market survey (市集調查): live per-character status and the recorded listings."""

import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from services import equip_stats, item_catalog, market_goto
from services.api_types import (
    MarketCurrentStall,
    MarketExcludeRequest,
    MarketGotoRequest,
    MarketGotoResult,
    MarketGoneRow,
    MarketItemSummary,
    MarketListing,
    MarketModeRequest,
    MarketSellerExcludeRequest,
    MarketSession,
    MarketStatus,
    MarketStallRow,
    MarketTotals,
    OkResponse,
    WalkPoint,
)
from services.market_db import classify_price, silver_value

router = APIRouter(prefix="/api", tags=["market"])


def _price(price: int) -> dict:
    kind, coins = classify_price(price)
    return {"price": price, "price_kind": kind, "coins": coins, "silver": silver_value(price)}


def _current(cur: dict | None, db) -> MarketCurrentStall | None:
    if cur is None:
        return None
    rows = []
    for r in cur["rows"]:
        attrs = json.loads(r["attrs"])
        rows.append(
            MarketStallRow(
                **_price(r["price"]),
                item_id=r["item_id"],
                count=r["count"],
                plus=attrs["plus"],
                stats=equip_stats.to_stats(attrs["stats"]),
                inlays=equip_stats.inlays(attrs["inlays"]),
                status=r["status"],
                old_count=r["old_count"],
                suspect=db is not None and db.is_bait(r["item_id"], r["price"]),
            )
        )
    return MarketCurrentStall(
        seller=cur["seller"],
        sign=cur["sign"],
        open=cur["open"],
        opened_at=cur["opened_at"],
        recorded_at=cur["recorded_at"],
        settle_s=cur["settle_s"],
        rows=rows,
        gone=[MarketGoneRow(**_price(price), item_id=item_id) for item_id, price in cur["gone"]],
        new=cur["new"],
        unchanged=cur["unchanged"],
        changed=cur["changed"],
    )


@router.get("/characters/{pid}/market/status", response_model=MarketStatus)
def market_status(pid: int, request: Request) -> MarketStatus:
    mgr = request.app.state.services.get("market_manager")
    if mgr is None:
        return MarketStatus(
            mode="auto",
            active=False,
            reason="off",
            stalls=[],
            log=[],
            session=MarketSession(stalls=0, new=0, reads=0),
        )
    s = mgr.status(pid)
    db = request.app.state.services.get("market_db")
    return MarketStatus(**{**s, "current": _current(s["current"], db)})


@router.put("/characters/{pid}/market/mode", response_model=OkResponse)
def market_mode(pid: int, body: MarketModeRequest, request: Request) -> OkResponse:
    mgr = request.app.state.services.get("market_manager")
    if mgr is not None:
        mgr.set_mode(pid, body.mode)
    return OkResponse(ok=True)


def _db(request: Request):
    db = request.app.state.services.get("market_db")
    if db is None:
        raise HTTPException(status_code=503, detail="market database unavailable")
    return db


def _names(ids: list[int]) -> dict[int, str]:
    return {m.item_id: m.name for m in item_catalog.lookup(ids)}


@router.get("/market/totals", response_model=MarketTotals)
def market_totals(request: Request) -> MarketTotals:
    return MarketTotals(**_db(request).totals())


# Sync on purpose: item_catalog loads on first use (~0.3 s).
@router.get("/market/items", response_model=list[MarketItemSummary])
def market_items(
    request: Request,
    q: str = Query("", description="Item name substring"),
    include_ended: bool = False,
    include_negotiate: bool = True,
) -> list[MarketItemSummary]:
    summaries = _db(request).item_summaries(
        include_ended=include_ended, include_negotiate=include_negotiate
    )
    names = _names([s["item_id"] for s in summaries])
    q = q.strip()
    out = []
    for s in summaries:
        name = names.get(s["item_id"], str(s["item_id"]))
        if q and q not in name:
            continue
        out.append(MarketItemSummary(**s, name=name))
    return out


@router.get("/market/items/{item_id}/listings", response_model=list[MarketListing])
def market_item_listings(
    item_id: int, request: Request, include_ended: bool = True
) -> list[MarketListing]:
    return [
        MarketListing(
            **{
                **r,
                "stats": equip_stats.to_stats(r["stats"]),
                "inlays": equip_stats.inlays(r["inlays"]),
                "enhance": equip_stats.enhance_bonus(r["item_id"], r["plus"]) if r["plus"] else [],
                "enhance_extra": equip_stats.enhance_extra(r["item_id"], r["plus"])
                if r["plus"]
                else [],
            }
        )
        for r in _db(request).listings_for_item(item_id, include_ended=include_ended)
    ]


@router.put("/market/listings/{listing_id}/excluded", response_model=OkResponse)
def market_exclude_listing(
    listing_id: int, body: MarketExcludeRequest, request: Request
) -> OkResponse:
    if not _db(request).set_listing_excluded(listing_id, body.excluded):
        raise HTTPException(status_code=404, detail=f"no listing {listing_id}")
    return OkResponse(ok=True)


@router.put("/market/sellers/excluded", response_model=OkResponse)
def market_exclude_seller(body: MarketSellerExcludeRequest, request: Request) -> OkResponse:
    _db(request).set_seller_excluded(body.seller, body.excluded)
    return OkResponse(ok=True)


@router.post("/market/listings/{listing_id}/goto", response_model=MarketGotoResult)
def market_goto_listing(
    listing_id: int, body: MarketGotoRequest, request: Request
) -> MarketGotoResult:
    """帶我去: walk a character on the listing's map to near its stall (services/market_goto.py)."""
    services = request.app.state.services
    listing = _db(request).get_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"no listing {listing_id}")
    wm, walker = services.get("worker_manager"), services.get("walk_manager")
    if wm is None or walker is None:
        raise HTTPException(status_code=503, detail="walking is unavailable")
    sample = wm.walk_sample(body.pid)
    if sample is None:
        raise HTTPException(status_code=409, detail="讀不到角色位置")
    stage_id, x, y = sample[0], sample[1], sample[2]
    if listing["stage_id"] is None or stage_id != listing["stage_id"]:
        raise HTTPException(status_code=409, detail=f"角色不在{listing['map'] or '那張地圖'}")
    note = None
    if listing["x"] is not None and listing["y"] is not None:
        stall = (listing["x"], listing["y"])
        mgr = services.get("market_manager")
        occupied = set()
        if mgr is not None:
            occupied = {
                (s["x"], s["y"])
                for s in mgr.status(body.pid)["stalls"]
                if s["x"] is not None and s["seller"] != listing["seller"]
            }
        goal, note = market_goto.pick_goal(stage_id, stall, (x, y), occupied)
    elif listing["viewer_x"] is not None and listing["viewer_y"] is not None:
        goal, note = (listing["viewer_x"], listing["viewer_y"]), "viewer position"
    else:
        raise HTTPException(
            status_code=422, detail="這筆上架沒有記錄到位置，重新逛一次這攤就會補上"
        )
    if goal is None:
        raise HTTPException(status_code=422, detail="攤位附近沒有走得到的格子")
    point = WalkPoint(x=goal[0], y=goal[1])
    if note == "already there":
        return MarketGotoResult(goal=point, note=note)
    return MarketGotoResult(goal=point, walk=walker.start(body.pid, goal), note=note)


def _tile(x: int | None, y: int | None) -> str:
    return f"({x}, {y})" if x is not None and y is not None else ""


def _fmt_time(t: float | None) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S") if t else ""


@router.get("/market/export.csv", responses={200: {"content": {"text/csv": {}}}})
def market_export(request: Request) -> Response:
    rows = _db(request).all_listings()
    names = _names(sorted({r["item_id"] for r in rows}))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "道具",
            "道具ID",
            "攤主",
            "招牌",
            "單價",
            "價格類型",
            "百萬官幣",
            "銀兩價",
            "數量",
            "強化",
            "屬性",
            "鑲嵌",
            "地圖",
            "攤位座標",
            "查看時位置",
            "首次看到",
            "最後看到",
            "已不在",
            "疑似誘餌價",
            "不採計",
        ]
    )
    kinds = {"silver": "銀兩", "coin": "官幣", "negotiate": "議價"}
    for r in rows:
        w.writerow(
            [
                names.get(r["item_id"], ""),
                r["item_id"],
                r["seller"],
                r["sign"],
                r["price"],
                kinds[r["price_kind"]],
                r["coins"] or "",
                r["silver"] if r["silver"] is not None else "",
                r["count"],
                r["plus"],
                " ".join(f"{st.label}{st.value}" for st in equip_stats.to_stats(r["stats"])),
                " ".join(f"{i.name}x{i.count}" for i in equip_stats.inlays(r["inlays"])),
                r["map"],
                _tile(r["x"], r["y"]),
                _tile(r["viewer_x"], r["viewer_y"]),
                _fmt_time(r["first_seen"]),
                _fmt_time(r["last_seen"]),
                _fmt_time(r["ended_at"]),
                "是" if r["suspect"] else "",
                {"seller": "整攤", "listing": "此筆"}.get(r["excluded"], ""),
            ]
        )
    # BOM so Excel opens the UTF-8 file with the right encoding.
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="market.csv"'},
    )
