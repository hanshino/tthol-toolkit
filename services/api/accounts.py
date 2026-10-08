from fastapi import APIRouter, HTTPException, Request

from services._mock import mock_accounts
from services.api_types import (
    Account,
    AccountCharacter,
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


@router.get("/accounts/characters", response_model=list[AccountCharacter])
async def account_characters(request: Request) -> list[AccountCharacter]:
    """Every known character with its account, for the 帳號分組 editor."""
    db = request.app.state.services.get("snapshot_db")
    if db is None:
        return []
    return [AccountCharacter(**r) for r in db.account_characters()]


@router.post("/accounts", response_model=Account)
async def create_account(body: CreateAccountRequest, request: Request) -> Account:
    db = request.app.state.services.get("snapshot_db")
    if db is None:
        return Account(account_id=99, name=body.name, character_count=0)
    acct_id = db.create_account(body.name)
    return Account(account_id=acct_id, name=body.name, character_count=0)


@router.put("/accounts/{account_id}", response_model=OkResponse)
async def rename_account(
    account_id: int, body: CreateAccountRequest, request: Request
) -> OkResponse:
    db = request.app.state.services.get("snapshot_db")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="empty name")
    if db is not None and not db.rename_account(account_id, name):
        raise HTTPException(status_code=409, detail="name taken")
    return OkResponse(ok=True)


@router.delete("/accounts/{account_id}", response_model=OkResponse)
async def delete_account(account_id: int, request: Request) -> OkResponse:
    db = request.app.state.services.get("snapshot_db")
    if db is not None and not db.delete_account(account_id):
        raise HTTPException(status_code=409, detail="account has characters")
    return OkResponse(ok=True)


@router.put("/characters/by-name/{name}/account", response_model=OkResponse)
async def set_character_account(
    name: str,
    body: SetCharacterAccountRequest,
    request: Request,
) -> OkResponse:
    db = request.app.state.services.get("snapshot_db")
    if db is None:
        return OkResponse(ok=True)
    if body.account_id is None:
        db.remove_character_account(name)
    else:
        db.set_character_account(name, body.account_id)
    return OkResponse(ok=True)
