# Map Navigation ("GPS") + Auto-Walk

## Overview

Give the read-only memory reader a GPS-like capability: from the character's current
map, compute a route to a chosen destination map (crossing map warps), display it as an
annotated turn-by-turn list, and — in a later phase — drive `auto_click` to physically
walk the character there.

This document covers the **validated** routing/planning half in detail (Phase 1, buildable
now from `tthol.sqlite`, no running game required) and scopes the auto-walk execution half
(Phases 0/2/3) which is gated on an empirical spike with the game running.

The feature stays **read-only** with respect to game memory. Movement is produced only by
synthesized clicks into the game window, never by writing process memory.

## Goals

- Compute a cross-map route (sequence of map hops) from the current map to a destination map.
- Annotate each hop as **walk** ("walk to map exit") or **teleport** ("talk to NPC «name»").
- Let the user pick a destination by searchable map name.
- Default to the fewest-hops / fastest route; offer a **pure-walking** toggle.
- (Later) auto-walk the route via background clicks, with closed-loop arrival detection.

## Non-Goals

- No within-map pathfinding around obstacles. The game is **click-to-move** (user-confirmed),
  so the game's own pathing handles local obstacle avoidance. We never need a collision grid.
- No precise navigation to an arbitrary tile inside the destination map. Destination
  granularity is "a map"; arriving anywhere on it counts.
- No reverse-engineering of the binary `.mpc`/`.shp` map files **in Phase 1**. (Finding 16 reopens
  this for Phase 2: `op_offset` indexes the `.mpc` section-3 exit geometry, making a one-time parse
  the highest-leverage source of warp coordinates. Pursuing it is an explicit user decision — see
  Phase 2 option A and Open Questions.)
- No writing to game memory.

## Validated Findings (the basis for this design)

All confirmed against the real `tthol.sqlite` (3023-row `map_warps`) and a live route test.

1. **`map_warps` is a usable directed graph.** Columns:
   `src_stage_id → dst_stage_id`, plus `warp_kind`, `warp_op`, `dst_tag`, `msg_file_no`,
   `msg_id`, `src_resolution`. 3023 edges; `warp_kind='auto'` (1407) vs `'dialogue'` (1616).
   The graph is **directed and largely asymmetric** — only ~25% of directed edges have a
   reverse. Routing must NOT assume bidirectionality.
2. **`auto` (pure-walking) edges are 100% trustworthy.** Validated route
   `莫愁谷入口(#1) → 崑崙草原(#3) → 青海原(#7) → 藏海村(#9) → 塞外沙漠東(#19) → 成都少城(#53)`
   was confirmed correct by an expert player.
3. **Coverage by edge-trust tier** (identical from `#1` and `#53` — same connected component):
   - `walk`-only (auto): **147 / 465 (32%)** — fully trustworthy.
   - walk + **NPC-bearing** teleport: **220 / 465 (47%)** — the *actionable* ceiling (every hop
     is a real NPC the user/auto-walker can interact with).
   - walk + (NPC-or-msg) teleport: **262 / 465 (56%)**.
   - walk + ALL non-instance teleport: **330 / 465 (71%)** — but the 47%→71% jump comes entirely
     from `teleport_orphan` edges (no NPC, no msg) that are not actionable and produce false
     routes. So the **realistic actionable target is ~47%**, growing via empirical verification.
   The teleport layer is **required** (walking alone is only 32%), but only its NPC-bearing
   subset is dependable.
4. **`dialogue` (teleport) edges are noisy and have no clean classifier flag.** Of the 1570
   dialogue edges to normal maps: 457 have an NPC speaker (`name_id`), 760 are pure warp
   actions (`msg=NULL`), 353 are no-speaker narrative lines (quest scripts). Example of a
   **false** edge that BFS happily abused: `青海原(#7) → 八門八窟(#39)`, `warp_op=3`,
   `msg='原來還別有洞天阿！！'` — a quest story line, **no real NPC exists** (expert-confirmed).
   The same `warp_op=3` also covers legitimate town teleports, so `warp_op` alone cannot
   separate them.
5. **Heuristic signals for classifying dialogue edges (no single flag suffices):**
   - `warp_op=98` (46 edges) + `dst_stage_id >= 1000` ⇒ **dungeon/instance** entry → exclude.
   - `src_stage_id IS NULL` ⇒ unusable for source-based routing → exclude. (Filtering these +
     instances loses **zero** normal-map coverage: 330 == 330.)
   - `name_id` resolves via **`npc_strings(id, name)`** (NOT the combat `npc` table). Names are
     strong signals: transport NPCs like `天外天馬夫`(coachman), `指路仙人`(guide),
     `仙島守衛`(guard) ⇒ likely real; `謎之聲`(mystery voice), `解任務npc`(quest NPC) ⇒ quest.
   - Message text patterns: `移動至…` / `回到…` / `傳送…` ⇒ likely real; narrative prose ⇒ quest.
   - `messages.triggers` encodes the warp as `[["0","0","1",warp_op,?,dst_stage,dst_tag]]`.
6. **Live position is readable; current map is by NAME.** Player `x,y` = int32 at
   `HP+416/+420`, **tile-scale** (sample 54,127; range matches `mission_refs.x/y` 1–225, NOT
   the pixel-scale `monster_spawns.x/y` up to 65509). Current map is obtained via
   `reader.locate_map_name()` (Big5 heap scan) → `map_db.stage_by_name()`. There is **no
   numeric map-id in memory**; `stage_by_name` uses `LIMIT 1`, a duplicate-name hazard.
7. **DB text is UTF-8.** Earlier "mojibake" was only the Windows cp950 console; the stored
   bytes decode cleanly as UTF-8. `map_db.py`'s existing UTF-8 `text_factory` is correct.
8. **No routing/pathfinding code exists yet.** `map_db.warps_from_stage()` is single-hop;
   `api/maps.py` returns single-map `MapInfo`; `MapAnalysis.tsx` is display-only with a
   non-clickable exit list. `auto_click.py` clicks only fixed 800×600 UI-button coords — there
   is **no game-world → screen-pixel transform** anywhere, and no screenshot/DPI handling.
9. **`src_stage_id` is unreliable for shared-dialogue (transport) NPCs.** Boatman/teleport NPCs
   that reuse one message file are all attributed to that file's stage, not where they stand.
   Example: every `成都碼頭船夫` / `無名村船夫` edge is recorded with `src_stage_id=37`
   (`地靈宮主迴廊` — whose real `auto` neighbours are 莫愁谷/龍門峽, nowhere near 成都). The NPC
   **name encodes the true location**: `成都碼頭船夫` stands at `成都碼頭(#168)`, `無名村船夫` at
   `無名村(#213)`. Correcting the source by name yields the real, expert-confirmed route
   `成都少城(#53) →walk→ 成都太城(#54) →walk→ 成都碼頭(#168) →[成都碼頭船夫]→ 無名村(#213)`
   instead of a nonsensical path through 地靈宮.
10. **The practical fast-travel network (馬夫/coachman + family) is NOT in `map_warps`.** It lives
    in dialogue **menus**: a 馬夫 message like `客倌，想去哪兒呢？` carries `message_options` whose
    `text` is `去檀泉別院` / `回到流星村` / `去飛雁山莊` and whose `jump_to` chains to the warp.
    `map_warps` resolved only 2 of the 6 馬夫 NPCs. So a `map_warps`-only router shows long walking
    paths where players actually hop 馬夫→馬夫. The expert's real `莫愁谷→成都` route
    (`莫愁谷馬夫 → 檀泉別院`, then `家族馬夫 → 成都`) is reconstructable **only by mining the menus**.
    Two wrinkles: (a) destination text uses char variants — `去檀泉別院` must fuzzy-match stage
    `檀泉別苑(#23)` (院↔苑); (b) **family (家族馬夫) destinations are level-gated** — the static DB
    lists the options (incl. 成都) but availability depends on the character's family level, which
    is runtime state (potentially readable from memory later).
11. **Instance dungeons are isolated components — not continuously routable.** `蝴蝶幽谷`
    (`二層#277`) and `深森秘徑(#272-275)` form one fully-isolated component: every warp into any
    of `#272-279` comes from another stage *inside* the cluster, and there is NO edge (walk,
    dialogue, menu, or `mission_ref`) from the overworld. A message names it `深森秘徑迷宮(地圖
    1720)` and an entry NPC `深森校尉(#31246)` exists but has **zero warp edges** — the dungeon is
    entered through an instance-creation mechanism that is not encoded as a traversable edge
    anywhere. So a continuous route `天外天 → 蝴蝶幽谷二層` is **impossible to compute** (the data
    has no path in), even though the 天外天 start reaches the full 360-node main component. This
    is the third destination category (after overworld maps and menu/hub teleports): **instance
    interiors**, reachable only via an instance NPC. (Same class as the `warp_op=98` 人勝莊 instances.)
12. **The DB is stale/incomplete; the system must self-heal via added edges.** `深森秘徑一層`
    is officially entered from `月泉之森(#265)` (LV155 + 氧氣罩 gated), but that **entry edge is
    missing** from `map_warps` — the DB even has the *exit* (`深森秘徑三層→月泉之森`) without the
    *entry*. New content (`[新增]` maps) and conditional/gated warps are simply not extracted.
    Critically, the rest of the path IS present: `天外天地下街(#170)` reaches `月泉之森` over the
    main component, and the dungeon interior `#272→…→蝴蝶幽谷二層(#277)` is intact — so injecting
    the single official edge `月泉之森→深森秘徑一層` makes the full 14-hop route compute end to end.
    Lesson: the override/learning layer is **not optional polish** — it is the mechanism that
    repairs DB gaps. A user-provided fact or an empirically-harvested transition adds the missing
    edge and the route completes.
13. **`特殊機關` is a 185-edge fake super-hub — exclude the `機關` class.** `練功窟二層(#66)` has
    185 `dialogue` edges via an NPC named `特殊機關` reaching nearly every map (莫愁谷, 崑崙草原,
    青海原, 飛雁山莊, 星月山道, …). This is a debug/special "exit to anywhere" mechanism, NOT a real
    travel option (the expert confirms "練功窟那邊沒有任何機關可以過去"). It was the **root cause of
    every false shortcut** seen earlier (閻王門, 曼陀羅 detours all routed through `練功窟二層`).
    Excluding NPC-name-contains-`機關` edges removes the poison. With them gone, the expert's real
    region `成都太城 → 楓林火山 → 月泉之森` is connected — though some exact edges there
    (`太城→楓林火山`, `楓林火山→月泉之森` by walk) are missing/mis-encoded (newer-content staleness,
    cf. Finding 12), so the override/harvest layer still has to fill that region's gaps.
14. **Walk routing must be FORWARD-DIRECTED — naive bidirectional walk is WRONG (corrected by
    adversarial audit).** An earlier draft of this finding said "treat `auto` as undirected";
    the audit (`_route_solver.py`, 8/8 routes PASS) disproved it. Of 594 one-way `auto` edges,
    **437 are dungeon/arena/instance EXITS** (isolated→overworld) — reversing them fabricates
    non-existent dungeon *entries*. Worse, many one-way edges are **recall/death-return** edges
    into the mega-hub `莫愁谷入口(#1)` (92–126 one-way incoming); reversing those invents
    `#1→anywhere` shortcuts that broke 4/6 routes. Reachability from `#53`: directed = **147**,
    naive-bidirectional = 409 (**+262 are all fabricated dungeon interiors**), SAFE-bidirectional
    (reverse only the 88 edges with both endpoints in the overworld SCC) = **147 (adds 0)**.
    **Correct rule:** `walk_only` uses the forward-directed `auto` graph (≈147 nodes ≈32%). Reverse
    only the 88 intra-overworld one-way edges (optional; 0 coverage gain, only alt-paths). NEVER
    reverse the 437 exit edges. The expert `成都城郊(#10)→天山通道(#266)→月泉之森(#265)` route works
    because `天山通道(#266)` is a **pure-source** node (0 incoming of any kind); its reverse-entry
    is allowed ONLY in `fastest` mode for pure-source overworld feeders — keeping `walk_only`
    correct (this is also why `無名村` stays walk-unreachable while `#265` is fastest-reachable).
    "Overworld" = the directed SCC containing `#53` (143 nodes), NOT the weakly-connected 409.
15. **Audit corrections & additions (`nav-data-audit`, 4-agent adversarial pass):**
    - **Recall class is not connectivity.** 76 `auto` rows with `dst=1, dst_tag=3,
      src_resolution='mpc_sec3'` across 58 sources are a global town-portal/recall — treat as a
      flag, exclude from the graph.
    - **Second debug super-hub: `九尾狐仙`** (80 edges, destination list identical to `特殊機關`).
      Add to the denylist. Excluding `機關`+`九尾狐仙` removes 265 dialogue edges, disconnects 0 maps.
    - **The biggest fake hubs are NAME-LESS** (`messages.name_id` NULL): `青海原(#7)` fans to 90
      distinct dsts, `陰風原(#90)` to 78. Name denylists can't catch them, and blanket-banning the
      source strands real stages (banning `#7` strands 39 island/dungeon maps). Use **edge-level
      redundant-edge pruning** (drop a dialogue edge only if its dst stays reachable without it),
      not node bans.
    - **Do NOT exclude `解任務npc` / `家族總管`** despite high fan-out — they are the *only*
      entrance to family/quest instances (`dst 1001-1171`: 人和莊/地文莊/天平莊…); excluding strands
      30 stages. Fake-hub signal = "destinations span unrelated regions", not raw fan-out.
    - **Override layer: add these expert-expected missing reverses** — `265→266`, `265→264`,
      `213→265`, `48→267`, `203→267`, `93→276`.
    - `蝴蝶幽谷四層(#279)` is a total orphan (0 edges) — permanently unreachable; flag, don't route.
    - **馬夫 menu mining is implementation-sensitive** (one impl found 5 edges, the audit's found
      0). Phase 1b must implement + unit-test it carefully; it doesn't affect the 6 core routes.
    - De-dup edges on `(src_stage_id, dst_stage_id, warp_kind)` — `map_warps` has duplicate rows.
16. **The in-map teleport-POINT `(x,y)` is NOT in the DB — it lives in the `.mpc` map files,
    indexed by `op_offset` (audit `warp-point-data-audit`, 4 agents, every number DB-verified).**
    The routing graph (which map → which map) is complete, but the *where to walk to cross over*
    tile is absent from **all 25 tables**. `map_warps` is a fully-decoded edge list with **zero
    coordinate columns**; its only per-edge numeric, `op_offset` (32..35520, 568 distinct, set only
    when `src_resolution='mpc_sec3'`), is a **byte offset into the source stage's external
    `map\stageNNN.mpc` section-3 blob** — a ready-made index to the exit-trigger geometry for all
    1407 walk edges, but the blob itself is not in the DB. The destination **arrival** tile is also
    absent: `dst_tag` (0..139) + `stages.appear_tag/safe_tag/cave_tag` are spawn-LABEL ids whose
    tile resolution likewise lives inside the `.mpc`. `monster_spawns` has `(x,y)` but **pixel-scale
    and combat-mobs only** (0/13 transit NPCs present); the `npc` table has no coords; and the
    warp→NPC→coord chain is broken (`messages.name_id` is the 30000+ `npc_strings` id-space, disjoint
    from `npc.id`/`monster_spawns.npc_id` — 0/30 dialogue warps resolved to a coord). The **only**
    DB-native tile-scale `(x,y)` is `mission_refs` (1438 nonzero rows, x 1..225 / y 4..181, matching
    player `HP+416/+420`), including real portal landmarks (`莫愁谷後山入口` map2 (108,152),
    `少林寺山門` map51 (65,161), `龍門峽` map32 (106,85)) — but it is **quest-marker-keyed with no
    join to warp edges** (only 1/3023 warps is `mission_ref`-resolved) and covers 90/712 maps, so it
    is usable **only as opportunistic hints, never authoritative**. **Authoritative DB coverage of
    exit/arrival tiles = 0%** (heuristic map-overlap hints touch ~37% of edges, guess-quality).
    This **confirms** the Phase-2 gap and reveals two real acquisition paths (see Phase 2): (a) parse
    `.mpc` section 3 via `op_offset` — **but the `.mpc` files are NOT in this repo** (`Glob **/*.mpc`
    = 0 hits; they ship with the game client, not the tool), the binary format is undecoded, and
    whether section-3 records embed `(x,y)` is plausible-but-unproven — and this path **reverses the
    current `.mpc` Non-Goal**; (b) learn-by-playing (fully in-hand). Do NOT build on `mission_refs`
    as a primary source.
17. **The `.mpc` format is decoded; NPC positions, the coordinate system, and per-map warp
    point-sets are recoverable now (`mpc-format-re`, 5-agent RE, 2026-06-08).** A `.mpc` is four
    regions: **(H) header** `[0,0x40)` — magic `MAP\0`, `W=u32@0x04`, `H=u32@0x08`, `hdr[6]@0x18` =
    tile-region end, `hdr[8]@0x20`; **(T) tile layer** `[0x40, hdr[6])` — `W·H` cells × **8 bytes**
    `(graphic:u32, attr:u32)`, cell at `0x40+(y·W+x)·8` (verified `region==W·H·8`, 566/566 — the
    earlier "40×40" was a misread; stage001 is 80×60); **(S2) placement layer** `[hdr[6], hdr[8])` —
    an array of **fixed 38-byte records** (`(hdr[8]−hdr[6]) % 38 == 0` for 642/642 files); **(S3) op
    script** at `hdr[8]+12` — variable records, warps at `+op_offset` with opcode `(3,0,0)` at
    `op_offset−12`, `dst_stage@+12`, `dst_tag@+16`, **no `(x,y)`**.
    - **Coordinate system LOCKED** (the load-bearing result): a placement record's tile is
      `tile_x = round(raw_x/40)`, `tile_y = H − round(raw_y/40)` — **the Y axis is flipped** (file
      origin bottom-left; tile/live/`mission_refs` origin top-left). This tile space **equals the live
      player coord (`HP+416/+420`) and `mission_refs.x/y`** — no conversion layer needed. Verified
      exact on a named NPC (拜神婆 `npc6078` raw(3176,956)→(79,138) = `mission_refs` (79,138)); 98% of
      57 NPC cross-checks within ±2 tiles (≈±1 jitter from the /40 rounding).
    - **S2 record fields** (u16 LE from record start): `+2 raw_x`, `+4 raw_y`, `+8` marker `0xC0`,
      `+10` aux (= warp/`dst_tag` id on type-102), `+14` **type/npc_id** (`103`=monster spawn→npc at
      `+18`; `101`=arrival/appear point; `102`=warp **source-trigger** zone; or a direct `6xxx/7xxx`
      village-NPC id), `+16` appear-tag (on type-101), `+18` spawn npc_id.
    - **Recoverable now:** (1) **every NPC's stand tile on every map** — verified 95/119 vs
      `mission_refs`, exact on named NPCs → **directly unblocks NPC/船夫/馬夫 teleport auto-walk** (we
      can find where the transit NPC stands and walk to it); (2) monster-spawn positions (= DB
      `monster_spawns`, 27/27 byte-exact on stage001); (3) **per-map warp point-set** — type-101
      (arrival) + type-102 (trigger) tiles, validated against portal landmarks at manhattan 1–2
      (龍門峽 (106,85)→(106,83); 密道入口 (104,78)→(104,79); 莫愁谷後山入口 (108,152)→(109,152)); (4)
      coarse per-map **walkability** via terrain-code flood-fill (single-code maps 100%; multi-code
      35/47) — a bonus for path validation; (5) warp destinations (already in `map_warps`).
    - **Walkability nuance:** `attr & 0x7f` is a **per-map** terrain/material palette index, NOT a
      global walkable bit (landmarks occupy ~45 distinct codes). `attr==0` is map-edge void on normal
      maps, but ~19 maps are 100% `attr==0` yet walkable → their collision lives in S2 (not yet
      decoded for that purpose).
    - **Still missing (the core gap):** the **1:1 join from a specific `map_warps` row to its specific
      type-102 trigger tile** — type-102 records outnumber auto warps 3–7×, and auto-warp `dst_tag` is
      mostly NULL, so no reliable key links a warp to one exit tile yet. The *set* of exit tiles per
      map is extractable; *which tile fires which warp* is not. (`dst_tag → arrival tile` likewise
      resolves only ~16–29% via type-101 `+16`.) The auto-warp trigger zone appears edge-implicit (no
      explicit rect).
    - **Most decisive next step:** **live ground truth** — capture the player tile (`HP+416/+420`) at
      the instant of an auto-warp and match it to the type-102 record it sits on; one clean datapoint
      pins the trigger→record mapping and the tag namespace. (Fallbacks: join S3 warp order
      `record_idx`/`op_offset` to source-map type-102 order; use a reverse warp's source trigger as an
      arrival proxy.) Caveat: `op_offset` aligns to the DB's build source (`E:\setting`); maps changed
      in `E:\new` may not align.

## Locked Design Decisions

- **Layered route graph:**
  - **L1 — walk** edges (`auto`): trusted; the only fully-reliable layer. **Forward-directed**
    (Finding 14) — NOT undirected; reverse only the 88 intra-overworld one-way edges, never the
    437 dungeon-exit edges. Pure-source feeder reverse-entry (e.g. `天山通道`) is `fastest`-only.
  - **L2 — direct dialogue teleports**: heuristic candidates from `map_warps`, labeled
    *unverified*, promoted/demoted by empirical play; orphan/quest excluded.
  - **L2-fast — 馬夫/coachman fast-travel network** (mined from dialogue menus, Finding 10):
    the practical inter-town travel layer; high priority once mined.
  - **L3 — family-base (家族馬夫) teleports**: level-gated, **opt-in** (user states family level,
    or read from memory in a later phase); off by default.
- **Route preference is switchable; default = fewest-hops / fastest** (uses walk + L2 + L2-fast,
  and L3 when enabled). A "pure walking" mode restricts to L1 only.

## Architecture

### Edge model & classification

A single function classifies each `map_warps` row into an `edge_class`:

| edge_class        | rule (first match wins)                                                  | used in routing |
|-------------------|--------------------------------------------------------------------------|-----------------|
| `unusable`        | `src_stage_id IS NULL` or `dst_stage_id IS NULL`                          | no              |
| `instance`        | `warp_op=98` or `dst_stage_id >= 1000`                                   | no              |
| `walk`            | `warp_kind='auto'`                                                       | **yes (L1)**    |
| `teleport_quest`  | `dialogue`, blacklisted, or quest-NPC keyword, or no-NPC narrative line  | no (shown dim)  |
| `teleport_orphan` | `dialogue`, **no NPC speaker AND no message text** (detached warp action) | **no by default** (not user-actionable) |
| `teleport_likely` | `dialogue` **with NPC speaker**, transport keyword or `移動至/回到/傳送` | **yes (L2)**    |
| `teleport_maybe`  | remaining `dialogue` **with NPC speaker**                                | yes if `mode≠walk_only`, flagged *unverified* |

**Why `teleport_orphan` is excluded (discovered via hard route tests):** 757 dialogue edges
have no NPC speaker *and* no message text — they are the execution half of a menu/quest-driven
warp whose trigger we cannot reconstruct from the DB. They are (a) **not user-actionable**
(there is no NPC to talk to, so neither the player nor the auto-walker can trigger them) and
(b) the source of nonsense shortcuts. Examples: `成都少城→…→練功窟二層→閻王門` and
`…→流星火島→杭州渡口` both abuse orphan warp-actions to fabricate a 3–4 hop "fastest" route
through unrelated training caves / islands. Routing over **walk + NPC-bearing teleport only**
eliminates them. (A few legitimate "return to town" warps like `八門八窟→成都` are also orphans,
but without their triggering NPC/menu we cannot make them actionable anyway — so excluding by
default is correct; they can still be promoted via the override store if empirically verified.)

Each edge carries metadata for display & weighting: `warp_kind`, `warp_op`, `dst_tag`,
`npc_name` (from `npc_strings` via `messages.name_id`), `msg` (trimmed), `confidence`
(0–1 from the heuristics), and a `verified` flag (from the override store).

NPC-name keyword lists (extend over time, all in `services/route_graph.py`):
- transport: `馬夫`, `船夫`, `指路`, `守衛`, `傳送`, `護衛`, `車夫`, `艄公`, `水手`
- exclude (quest/debug, → `teleport_quest`): `謎之聲`, `解任務`, `旁白`, `機關` (the `特殊機關`
  super-hub, Finding 13)

**Source correction for transport NPCs (Finding 9).** Before inserting a `dialogue` edge,
re-derive its effective `src_stage_id` from the NPC name: if the name matches
`(?P<place>.+?)(船夫|馬夫|車夫)$` and `<place>` exactly matches a `stages.name`, set the edge's
source to that stage. This relocates `成都碼頭船夫` edges from the bogus `地靈宮主迴廊(#37)` to
`成都碼頭(#168)` where the NPC actually stands, so the router connects the real
walk-then-boat route. The rule only fires on an exact stage-name match (otherwise the original
`src_stage_id` is kept), and the override store can correct any residual mistakes.

### Override / learning store

A new persisted store records user/empirical corrections so the graph improves over time.
Add a table to the existing user DB (`%APPDATA%\御心鑒\snapshots.db`, owned by
`services/snapshot_db.py` patterns) rather than a new file:

```sql
CREATE TABLE IF NOT EXISTS nav_edge_overrides (
  src_stage_id INTEGER NOT NULL,
  dst_stage_id INTEGER NOT NULL,
  state        TEXT NOT NULL,   -- 'verified' | 'blacklisted' | 'added'
  action       TEXT,            -- for 'added': 'walk' | 'teleport' | 'gated'
  npc_name     TEXT,            -- entry NPC, if any
  req_level    INTEGER,         -- conditional gate, e.g. 155
  req_item     TEXT,            -- conditional gate, e.g. '氧氣罩'
  source       TEXT NOT NULL,   -- 'user' | 'auto_detected'
  updated_at   TEXT NOT NULL,
  PRIMARY KEY (src_stage_id, dst_stage_id)
);
```

- `verified` edges are always usable and shown without the *unverified* badge.
- `blacklisted` edges (e.g. `7→39`) are excluded from routing regardless of class.
- `added` edges (Finding 12) inject connectivity the DB lacks — new/gated entries like
  `月泉之森→深森秘徑一層 (LV155, 氧氣罩)`. They carry optional `req_level`/`req_item` gates.
- Empirical promotion (Phase 3): when the player actually changes map at/after an NPC
  interaction, auto-insert `verified` (or `added` if the edge was absent). When a routed
  teleport hop fails to change the map within a timeout, auto-insert `blacklisted`.

**Conditional gates + we know the character.** Because this is a memory reader, the character's
`等級` (level, `HP-36` in `knowledge.json`) is already live. Edges with `req_level`/`req_item`
can therefore be auto-checked: routes are still shown, but a hop the character cannot yet use
(e.g. LV155 entry when the character is lower, or a missing `氧氣罩`) is flagged
"需 LV155 / 氧氣罩" rather than silently assumed passable.

### Routing

`services/route_graph.py`:
- Load `map_warps` once at startup into a directed adjacency `dict[int, list[Edge]]`
  (join `npc_strings` for names), apply classification + overrides.
- `find_route(src_stage_id, dst_stage_id, mode)` → ordered `list[RouteHop]`.
  - `mode='fastest'`: Dijkstra with weights `walk=1.0`, `teleport`+`verified=1.0`,
    `teleport_likely=1.2`, `teleport_maybe=2.0` (gentle bias toward trusted edges while still
    minimizing hops). Excludes `instance`/`unusable`/`teleport_quest`/`teleport_orphan`/blacklisted.
    Excluding `teleport_orphan` is what keeps "fastest" routes sane (no fabricated training-cave
    shortcuts).
  - `mode='walk_only'`: BFS over `walk` edges only.
- Return `None` (unreachable) is a valid, displayed result.
- **Instance-destination detection (Finding 11).** Precompute connected components of the usable
  graph. If the destination is in a different component from the overworld (e.g. an instance
  interior like `蝴蝶幽谷`), do NOT return a bogus path — return a special result
  `{reachable:false, reason:'instance_interior', instance_name, entry_npc?}` so the UI can say
  "目的地在副本內,需從副本入口進入" plus, when known, the entry NPC and the internal floor path.

### API (`services/api/route.py`, registered in `services/api/__init__.py`)

| Method & path                         | Purpose |
|---------------------------------------|---------|
| `GET /api/maps/search?q=<text>`       | Destination picker: stage-name substring search → `[{stage_id, name}]` |
| `GET /api/route?to_stage=<id>&mode=`  | Route from the live current map to `to_stage`. `mode` ∈ `fastest`(default)/`walk_only` |
| `POST /api/route/feedback`            | Body `{src_stage_id, dst_stage_id, state, action?, npc_name?, req_level?, req_item?}` → upsert into `nav_edge_overrides` (supports verify / blacklist / **add edge**) |

`RouteHop` (add to `services/api_types.py`):
`{ from_stage_id, from_name, to_stage_id, to_name, action: 'walk'|'teleport', npc_name?, unverified: bool }`.
`RouteResponse`: `{ src_stage_id, dst_stage_id, mode, reachable: bool, hops: RouteHop[] }`.

Start node = current `stage_id`: reuse `locate_map_name → stage_by_name`. **Harden the
`LIMIT 1` duplicate-name hazard** (see Open Questions); for v1, log a warning when the
current map name is ambiguous.

### UI (`webui/src/pages/CharDetail/MapAnalysis.tsx`)

- Add a **destination picker** (debounced search box → `/api/maps/search`).
- Add a **route panel** rendering ordered hops: walk icon 🥾 vs teleport icon 🚀 + NPC name,
  an *未驗證* badge on `teleport_maybe`, and per-hop ✓/✗ buttons that POST `/api/route/feedback`
  (✓ verify, ✗ blacklist).
- Add a **mode toggle**: 「最快(含傳送)」 / 「純走路」.
- Keep all user-facing strings Chinese in the `.tsx`; regenerate `webui/src/api/schema.ts`
  from `/openapi.json` via `scripts/gen_openapi.py` (do not hand-edit).

## Phases

### Phase 1 — Routing engine + classification + UI  (buildable now, no game)

Everything in Architecture above. Fully validated; ships standalone value (a GPS route
display) even before auto-walk exists. **This is the first implementation plan.** Sub-steps:

- **1a — Core graph + routing**: load `map_warps`, classify edges (incl. orphan exclusion +
  Finding-9 source correction), Dijkstra/BFS, `/api/route`, `/api/maps/search`, UI route panel.
- **1b — 馬夫 fast-travel mining (L2-fast, Finding 10)**: a builder that scans dialogue menus
  (`messages` + `message_options`) for transport-NPC options (`去X`/`回到X`/`前往X`), resolves the
  destination to a stage with 院↔苑-tolerant fuzzy matching, sources the edge at the NPC's
  location (name rule), and merges these into the graph. Without 1b the router shows
  impractical long walks instead of the real 馬夫 hops, so 1b is high priority.
- **1c — family layer (L3)**: include `家族馬夫` level-gated edges behind an opt-in toggle
  ("family base level N"). Reading family level from memory is deferred to a later phase.

### Phase 0 — Auto-walk spike  (needs the game running; do in parallel)

`scripts/spike_clicktomove.py` — a throwaway probe answering the make-or-break unknowns:
1. **Does background `PostMessageW` click-to-move move the character?** Click several client
   points; read `HP+416/+420` before/after. Success = coords shift toward the clicked point.
   Also test whether a prior `WM_MOUSEMOVE` and/or `bring_window_to_front()` is required.
2. **World ↔ screen calibration.** Record start world `(x,y)`; click a known client pixel;
   wait until movement stops; record end world `(x,y)`. Repeat for several points → solve
   `pixels_per_tile` and the projection (orthogonal grid vs isometric), and verify the camera
   is player-centered. Output a `world_to_screen(tx, ty)` function + constants.

**Exit criteria:** if (1) fails, auto-walk pivots to synthesized keyboard input or foreground
clicking and is re-evaluated; if it succeeds, Phases 2/3 proceed with the calibrated transform.

### Phase 2 — Warp exit coordinates  (pending Phase 0; the auto-walk data gap)

The exit-trigger `(x,y)` on each source map (and the arrival tile on each destination map) is
**not in `tthol.sqlite`** — confirmed authoritatively absent across all 25 tables (Finding 16).
This is the make-or-break data gap for physical auto-walk: routing knows the map *sequence*, but
not *where on each map to walk* to cross over. Three acquisition strategies, in recommended order:

- **(B) Learn-by-playing (default, fully in-hand).** A nav-poll records the player's `(x,y)` on the
  source map in the instant before `map_name` changes, storing
  `warp_exit_coord(src_stage_id, dst_stage_id) → (x,y)`. Needs nothing but the live read we already
  have (`HP+416/+420`); coverage grows with play. This is the baseline that always works.
- **(A) Parse the `.mpc` files (DONE — the format is decoded; see Finding 17).** A 5-agent RE pass
  (2026-06-08) located the files (`E:\setting\MAP`, 566 valid; DB build source) and decoded the
  4-section format end to end. **The live-readable coordinate system, NPC stand tiles, per-map warp
  point-sets, and a coarse walkability layer are all recoverable now**; the one remaining gap is the
  1:1 join from a specific warp to its specific exit tile (Finding 17). This path is no longer a gamble
  — it is the primary data source, with a clear `.mpc` parser to build. **Reverses the `.mpc` Non-Goal**.
- **(C) Live memory scan (later enhancement).** The loaded map's warp objects are in memory; reuse
  the project's object-RE techniques to read exit zones directly. Heaviest; defer.

Recommendation: ship **(B)** as the dependable baseline; run the **(A) spike** opportunistically —
if it decodes, it leapfrogs months of play-harvesting. `mission_refs` tiles are **not** a primary
source (no edge join, 90/712-map coverage) — at most a display hint.

### Phase 3 — Closed-loop cross-map executor  (pending 0 + 2)

A dedicated fast nav loop (~300 ms; the 3 s snapshot poll is too slow for closed-loop
walking). Per hop:
- **walk hop:** `walk_to(exit_x, exit_y)` (click → poll → re-click until close) → detect
  `map_name` change → advance; on timeout, mark progress stalled and stop.
- **teleport hop:** (later) requires the NPC's live on-map position to click it — out of scope
  until NPC memory positions are reverse-engineered; until then teleport hops are shown as
  manual "talk to «NPC»" instructions the user performs.

## Files to Create / Modify

| File | Action | Description |
|------|--------|-------------|
| `services/route_graph.py` | Create | Graph load, edge classification, source correction, Dijkstra/BFS routing |
| `services/fast_travel.py` | Create (1b) | Mine 馬夫/transport fast-travel edges from dialogue menus (`messages`+`message_options`); 院↔苑 fuzzy stage matching |
| `services/nav_store.py` *(or extend `snapshot_db.py`)* | Create/Modify | `nav_edge_overrides` table CRUD |
| `services/api/route.py` | Create | `/api/route`, `/api/maps/search`, `/api/route/feedback` |
| `services/api/__init__.py` | Modify | Register the route router |
| `services/api_types.py` | Modify | `RouteHop`, `RouteResponse`, feedback body models |
| `webui/src/pages/CharDetail/MapAnalysis.tsx` | Modify | Destination picker, route panel, mode toggle, feedback buttons |
| `webui/src/api/schema.ts` | Regenerate | Via `scripts/gen_openapi.py` (do not hand-edit) |
| `tests/test_route_graph.py` | Create | Graph build + routing + classification fixtures |
| `scripts/spike_clicktomove.py` | Create (Phase 0) | Click-to-move + world↔screen calibration probe |

## Testing (Phase 1)

Unit tests against the real `tthol.sqlite` as a fixture:
- `find_route(1, 53, 'walk_only')` returns the confirmed 5-hop walking route
  `1→3→7→9→19→53`.
- `find_route(1, 53, 'fastest')` is ≤ the walking hop count and uses no `instance`/`unusable`
  edge.
- Edge `7→39` classifies as `teleport_quest` and never appears in a `fastest` route.
- Classification: `warp_op=98` / `dst>=1000` → `instance`; `src IS NULL` → `unusable`;
  no-NPC + no-msg `dialogue` → `teleport_orphan`; a `天外天馬夫`/`指路仙人` edge → `teleport_likely`.
- Orphan-shortcut guard: `find_route(53, 207, 'fastest')` must NOT use the
  `醉楓林→練功窟二層→閻王門` orphan path; with orphans excluded it falls back to a walk/NPC route.
- Unreachable handling: `find_route(53, 50, ...)` (`無名島`, zero incoming warp edges) returns
  `reachable=false` for both modes without error.
- Instance detection: `find_route(any_overworld, 277, ...)` (`蝴蝶幽谷二層`) returns
  `reason='instance_interior'` (its component `#272-279` is disjoint from the overworld), NOT a
  fabricated path. The 天外天 start still reaches the 360-node main component.
- Teleport inclusion + source correction (positive): with the Finding-9 name correction
  applied, `find_route(53, 213, 'fastest')` (`無名村`) returns
  `成都少城(53) →walk→ 成都太城(54) →walk→ 成都碼頭(168) →[成都碼頭船夫]→ 無名村(213)` — the boat
  hop is `teleport_likely` (`船夫` keyword) and is sourced at `#168`, NOT the data's `#37`.
  `find_route(53, 213, 'walk_only')` returns `reachable=false`. (Expert-confirmed real route.)
- Reachability sanity: `walk_only` reaches 147 normal maps from #1; `fastest` (walk + NPC
  teleport, orphans excluded) reaches ~220 — NOT 330.
- Directed-graph guard: a route A→B existing does not imply B→A.

## Open Questions / Risks

- **Click-to-move via background PostMessage is unproven for the world canvas** (works for UI
  buttons today). Phase 0 spike decides the whole auto-walk approach. *(Decisive.)*
- **Duplicate stage names** make `stage_by_name LIMIT 1` ambiguous for the start node; a
  numeric map-id has not been found in memory. v1 logs a warning; hardening is future work.
- **Teleport NPC interaction** (Phase 3) needs the NPC's live on-map coordinate to click —
  not yet reverse-engineered. Until then teleport hops are manual instructions.
- **Polling cadence**: navigation needs a ~300 ms position read separate from the 3 s snapshot
  loop; ensure it doesn't contend with the existing worker.
- Heuristic classification will mislabel some edges; the override store + *unverified* badge
  + per-hop ✓/✗ feedback is the mitigation, not a perfect upfront classifier.
- **Warp exit/arrival coordinates are absent from the DB (Finding 16).** Auto-walk needs them.
  Decision required: pursue the **`.mpc` section-3 parse** (highest leverage, but the files aren't
  in the repo — the game install must be located first — and the format/(x,y)-presence is unproven;
  reverses the `.mpc` Non-Goal), or rely solely on **learn-by-playing** (slower but certain). A cheap
  `.mpc` spike would de-risk the parse path before committing. *(Gates Phase 2/3 auto-walk.)*
