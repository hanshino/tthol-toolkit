const PETAL = 'M16 14 Q17.7 11.9 16 9.6 Q14.3 11.9 16 14Z';

/** Brand mark: a Han bronze mirror (鑒) in a cinnabar seal frame; simplified from icon.png. Keep in sync with public/favicon.svg. */
export function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" role="img" aria-label="御心鑒" style={{ flex: 'none' }}>
      <rect x="0.5" y="0.5" width="31" height="31" rx="7" fill="#0f171c" />
      <rect x="4" y="4" width="24" height="24" rx="1" fill="none" stroke="#c4473d" strokeWidth="2" />
      <circle cx="16" cy="16" r="8.4" fill="#2c3a30" stroke="#c9a86a" strokeWidth="2.6" />
      <g fill="#d8bd82">
        {[0, 90, 180, 270].map(a => <path key={a} d={PETAL} transform={`rotate(${a} 16 16)`} />)}
        <circle cx="16" cy="16" r="1.9" />
      </g>
    </svg>
  );
}
