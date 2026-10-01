import { useState, type ReactNode } from 'react';
import type { Avatar } from '../api/types';

// Head portrait from paper-doll layers (hair, then cap) sharing one anchor
// point. Mirrored layers flip about their anchor, so their box starts
// (width - anchor_x) left of it instead of anchor_x. Shows `fallback` when
// there are no layers or an image fails to load (offline, missing art).
export function DollAvatar({ avatar, size, label, fallback }: {
  avatar: Avatar; size: number; label: string; fallback: ReactNode;
}) {
  // Remember which layer set failed, so a new outfit gets a fresh try.
  const key = avatar.layers.map((l) => l.src).join('|');
  const [brokenKey, setBrokenKey] = useState<string | null>(null);
  if (brokenKey === key || avatar.layers.length === 0) return <>{fallback}</>;

  const boxes = avatar.layers.map((l) => ({
    l,
    x: -(avatar.mirror ? l.width - l.anchor_x : l.anchor_x),
    y: -l.anchor_y,
  }));
  const minX = Math.min(...boxes.map((b) => b.x));
  const minY = Math.min(...boxes.map((b) => b.y));
  const w = Math.max(...boxes.map((b) => b.x + b.l.width)) - minX;
  const h = Math.max(...boxes.map((b) => b.y + b.l.height)) - minY;
  // Whole-number upscales keep pixel art crisp; a downscale has to smooth.
  const fit = Math.min(size / w, size / h);
  const scale = fit >= 1 ? Math.floor(fit) : fit;

  return (
    <span className="doll-avatar" role="img" aria-label={label} style={{ width: size, height: size }}>
      <span
        className="doll-avatar-stage"
        data-pixelated={scale >= 1 || undefined}
        style={{ width: w, height: h, transform: `translate(-50%, -50%) scale(${scale})` }}
      >
        {boxes.map(({ l, x, y }) => (
          <img
            key={l.src} src={l.src} alt="" draggable={false} onError={() => setBrokenKey(key)}
            style={{
              left: x - minX, top: y - minY, width: l.width, height: l.height,
              transform: avatar.mirror ? 'scaleX(-1)' : undefined,
            }}
          />
        ))}
      </span>
    </span>
  );
}
