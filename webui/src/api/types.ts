// Named type aliases over the openapi-typescript generated `schema.ts`.
// Regenerate `schema.ts` with `npm run gen-types`; this file is hand-written.
import type { components } from './schema';

type S = components['schemas'];

export type Account = S['Account'];
export type BackupImportResult = S['BackupImportResult'];
export type AutoClickConfig = S['AutoClickConfig'];
export type AutoClickStatus = S['AutoClickStatus'];
export type Avatar = S['Avatar'];
export type AutoClickTestRequest = S['AutoClickTestRequest'];
export type BuffInfo = S['BuffInfo'];
export type Character = S['Character'];
export type CharacterDetail = S['CharacterDetail'];
export type CharacterRow = S['CharacterRow'];
export type CharacterStats = S['CharacterStats'];
export type ConnectOptions = S['ConnectOptions'];
export type ConnectRequest = S['ConnectRequest'];
export type ConnectResult = S['ConnectResult'];
export type ClientErrorRequest = S['ClientErrorRequest'];
export type CreateAccountRequest = S['CreateAccountRequest'];
export type DamageEvent = S['DamageEvent'];
export type DamageSnapshot = S['DamageSnapshot'];
export type DamageStatus = S['DamageStatus'];
export type DamageSummary = S['DamageSummary'];
export type DamageTarget = S['DamageTarget'];
export type DiagEventModel = S['DiagEventModel'];
export type DiagSummary = S['DiagSummary'];
export type EquipSlot = S['EquipSlot'];
export type ErrorInfo = S['ErrorInfo'];
export type Item = S['Item'];
export type ItemMeta = S['ItemMeta'];
export type ItemStat = S['ItemStat'];
export type Inlay = S['Inlay'];
export type VerboseState = S['VerboseState'];
export type MapInfo = S['MapInfo'];
export type MapMonster = S['MapMonster'];
export type MapWarp = S['MapWarp'];
export type SpawnPoint = S['SpawnPoint'];
export type StageInfo = S['StageInfo'];
export type Minimap = S['Minimap'];
export type MinimapExit = S['MinimapExit'];
export type MinimapExitOption = S['MinimapExitOption'];
export type MinimapNpc = S['MinimapNpc'];
export type MinimapSpawn = S['MinimapSpawn'];
export type MinimapRegion = S['MinimapRegion'];
export type WalkPlan = S['WalkPlan'];
export type WalkStatus = S['WalkStatus'];
export type TreasurySummary = S['TreasurySummary'];
export type TreasuryItem = S['TreasuryItem'];
export type TreasuryHolder = S['TreasuryHolder'];
export type OkResponse = S['OkResponse'];
export type Position = S['Position'];
// /ws/pos frame (WebSocket, so not in the OpenAPI schema): positions that moved, by pid.
export type PositionFrame = { pos: Record<string, Position> };
export type SaveSnapshotRequest = S['SaveSnapshotRequest'];
export type SaveSnapshotResult = S['SaveSnapshotResult'];
export type SetCharacterAccountRequest = S['SetCharacterAccountRequest'];
export type SkillInfo = S['SkillInfo'];
export type SnapshotRow = S['SnapshotRow'];
export type Vitals = S['Vitals'];
export type WorldSnapshot = S['WorldSnapshot'];

// Pulled out of the link literal union for convenience
export type LinkStatus = CharacterRow['link'];
export type MarketMode = S['MarketStatus']['mode'];
export type MarketStatus = S['MarketStatus'];
export type MarketCurrentStall = S['MarketCurrentStall'];
export type MarketStallRow = S['MarketStallRow'];
export type MarketGoneRow = S['MarketGoneRow'];
export type MarketStallInView = S['MarketStallInView'];
export type MarketLogEntry = S['MarketLogEntry'];
export type MarketTotals = S['MarketTotals'];
export type MarketItemSummary = S['MarketItemSummary'];
export type MarketListing = S['MarketListing'];
export type StatSimExport = S['StatSimExport'];
