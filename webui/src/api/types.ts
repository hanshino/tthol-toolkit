// Named type aliases over the openapi-typescript generated `schema.ts`.
// Regenerate `schema.ts` with `npm run gen-types`; this file is hand-written.
import type { components } from './schema';

type S = components['schemas'];

export type Account = S['Account'];
export type AutoClickConfig = S['AutoClickConfig'];
export type AutoClickStatus = S['AutoClickStatus'];
export type AutoClickTestRequest = S['AutoClickTestRequest'];
export type BuffInfo = S['BuffInfo'];
export type Character = S['Character'];
export type CharacterDetail = S['CharacterDetail'];
export type CharacterRow = S['CharacterRow'];
export type CharacterStats = S['CharacterStats'];
export type ConnectOptions = S['ConnectOptions'];
export type ConnectRequest = S['ConnectRequest'];
export type ConnectResult = S['ConnectResult'];
export type CreateAccountRequest = S['CreateAccountRequest'];
export type Item = S['Item'];
export type MapInfo = S['MapInfo'];
export type MapMonster = S['MapMonster'];
export type MapWarp = S['MapWarp'];
export type SpawnPoint = S['SpawnPoint'];
export type StageInfo = S['StageInfo'];
export type TreasurySummary = S['TreasurySummary'];
export type TreasuryItem = S['TreasuryItem'];
export type TreasuryHolder = S['TreasuryHolder'];
export type OkResponse = S['OkResponse'];
export type Position = S['Position'];
export type SaveSnapshotRequest = S['SaveSnapshotRequest'];
export type SaveSnapshotResult = S['SaveSnapshotResult'];
export type SetCharacterAccountRequest = S['SetCharacterAccountRequest'];
export type SnapshotRow = S['SnapshotRow'];
export type Vitals = S['Vitals'];
export type WorldSnapshot = S['WorldSnapshot'];

// Pulled out of the link literal union for convenience
export type LinkStatus = CharacterRow['link'];
