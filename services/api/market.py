"""Market survey (市集調查): live per-character status and the recorded listings."""

import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from services import equip_stats, item_catalog
from services.api_types import (
    MarketCurrentStall,
    MarketExcludeRequest,
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
            "單價",
            "價格類型",
            "百萬官幣",
            "銀兩價",
            "數量",
            "強化",
            "屬性",
            "鑲嵌",
            "地圖",
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
                r["price"],
                kinds[r["price_kind"]],
                r["coins"] or "",
                r["silver"] if r["silver"] is not None else "",
                r["count"],
                r["plus"],
                " ".join(f"{st.label}{st.value}" for st in equip_stats.to_stats(r["stats"])),
                " ".join(f"{i.name}x{i.count}" for i in equip_stats.inlays(r["inlays"])),
                r["map"],
                _fmt_time(r["first_seen"]),
                _fmt_time(r["last_seen"]),
                _fmt_time(r["ended_at"]),
                "是" if r["suspect"] else "",
                {"seller": "整攤", "listing": "此筆"}.get(r["excluded"], ""),
            ]
        )
    # BOM so Excel opens the UTF-8 file with the right encoding.
    return Response(
        content="﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="market.csv"'},
    )
