import threading

from services.api_types import SupplyStatus, WithdrawConfig
from services.guard import GuardStore
from services.hook_caps import FEATURES
from services.withdraw import HOST, SECTION, WithdrawManager

BOOK, ORE = 31001, 1002


class FakeSupply:
    def __init__(self) -> None:
        self.hosts: list[str] = []
        self.runs: list[dict] = []
        self.busy = False
        self.done = threading.Event()

    def add_host(self, text: str) -> None:
        self.hosts.append(text)

    def status(self, pid):
        return SupplyStatus(running=self.busy)

    def running(self, pid):
        return self.busy

    def run(self, pid, stop, host=None, withdraw=None, seated=False):
        self.runs.append({"host": host, "withdraw": withdraw, "seated": seated})
        self.done.set()


class FakeChannel:
    def __init__(self, bag, warehouse=None) -> None:
        self.bag = bag
        self.warehouse = warehouse or []

    def send(self, pid, line):
        if line == "bag":
            return {"ok": True, "bag": [{"item": i, "count": n} for i, n in self.bag]}
        if line == "warehouse":
            return {"ok": True, "items": [{"item": i, "count": n} for i, n in self.warehouse]}
        return {"ok": False}


class FakeCaps:
    def cached(self, pid):
        return frozenset(FEATURES["withdraw"])


def make(bag, warehouse=None, snapshot=None, items=(BOOK,), bag_slots=40, **kw):
    store = GuardStore()
    store.save_section("天外", SECTION, WithdrawConfig(items=list(items), bag_slots=bag_slots))
    supply = FakeSupply()
    recorded = []
    mgr = WithdrawManager(
        supply=supply,
        character_name=lambda pid: "天外",
        channel=FakeChannel(bag, warehouse),
        store=store,
        hook_caps=FakeCaps(),
        item_info=lambda i: ({BOOK: "天外天秘笈", ORE: "鐵礦"}[i], False, False, "book"),
        warehouse_snapshot=lambda name: snapshot,
        account_name=lambda name: "華沁 / 天外",
        record_bag=lambda name, items: recorded.append((name, items)),
        **kw,
    )
    return mgr, supply, recorded


def test_registers_as_a_supply_host():
    _mgr, supply, _ = make([])
    assert supply.hosts == [HOST]


def test_start_takes_the_list_with_the_free_slots():
    mgr, supply, recorded = make([(ORE, 5), (ORE, 3)], bag_slots=10)
    assert mgr.start(1).ok
    assert supply.done.wait(2)
    for _ in range(100):
        if not mgr.running(1):
            break
        threading.Event().wait(0.01)
    assert supply.runs == [{"host": HOST, "withdraw": (frozenset({BOOK}), 8), "seated": True}]
    assert recorded == [("天外", [{"item_id": ORE, "qty": 5}, {"item_id": ORE, "qty": 3}])]


def test_start_refuses_an_empty_list_a_full_bag_or_a_busy_character():
    mgr, _s, _ = make([], items=())
    assert mgr.start(1).reason == "白名單是空的"
    mgr, _s, _ = make([(ORE, 1)] * 40)
    assert mgr.start(1).reason == "背包沒有空格"
    mgr, supply, _ = make([])
    supply.busy = True
    assert not mgr.start(1).ok
    mgr, _s, _ = make([], busy=lambda pid: "這隻角色正在登塔")
    assert mgr.start(1).reason == "這隻角色正在登塔"


def test_view_counts_from_the_accounts_snapshot_first():
    snap = {
        "character": "華沁",
        "scanned_at": "2026-10-08T15:12:40",
        "items": [{"item_id": BOOK, "qty": 1}, {"item_id": BOOK, "qty": 1}],
    }
    mgr, _s, _ = make([(BOOK, 1)], warehouse=[(ORE, 9)], snapshot=snap)
    v = mgr.view(1)
    assert v.warehouse_from == "snapshot" and v.warehouse_holder == "華沁"
    assert v.account == "華沁 / 天外" and v.hook_ready
    row = v.rows[0]
    assert (row.name, row.bag, row.warehouse, row.stacks) == ("天外天秘笈", 1, 2, 2)
    assert [w.item_id for w in v.warehouse] == [BOOK]


def test_view_falls_back_to_the_live_warehouse_then_to_unknown():
    mgr, _s, _ = make([], warehouse=[(BOOK, 1)])
    v = mgr.view(1)
    assert v.warehouse_from == "live" and v.rows[0].warehouse == 1
    mgr, _s, _ = make([])
    v = mgr.view(1)
    assert v.warehouse_from is None and v.rows[0].warehouse is None


def test_save_drops_duplicates():
    mgr, _s, _ = make([])
    saved = mgr.save(1, WithdrawConfig(items=[BOOK, ORE, BOOK]))
    assert saved.items == [BOOK, ORE]
    assert mgr.config("天外").items == [BOOK, ORE]
