from fastapi import APIRouter, Request

from services._mock import mock_accounts
from services.api_types import (
    Account,
    CreateAccountRequest,
    OkResponse,
    SetCharacterAccountRequest,
)

router = APIRouter(prefix="/api", tags=["accounts"])


@router.get("/accounts", response_model=list[Account])
async def list_accounts(request: Request) -> list[Account]:
    db = request.app.state.services.get("snapshot_db")
    if db is None:
        return mock_accounts()
    rows = db.list_accounts()
    counts = {r["account_id"]: r["count"] for r in db.account_character_counts()}
    return [
        Account(
            account_id=r["id"],
            name=r["name"],
            character_count=counts.get(r["id"], 0),
        )
        for r in rows
    ]


@router.post("/accounts", response_model=Account)
async def create_account(body: CreateAccountRequest, request: Request) -> Account:
    db = request.app.state.services.get("snapshot_db")
    if db is None:
        return Account(account_id=99, name=body.name, character_count=0)
    acct_id = db.create_account(body.name)
    return Account(account_id=acct_id, name=body.name, character_count=0)


@router.put("/characters/by-name/{name}/account", response_model=OkResponse)
async def set_character_account(
    name: str,
    body: SetCharacterAccountRequest,
    request: Request,
) -> OkResponse:
    db = request.app.state.services.get("snapshot_db")
    if db is None:
        return OkResponse(ok=True)
    db.set_character_account(name, body.account_id)
    return OkResponse(ok=True)
