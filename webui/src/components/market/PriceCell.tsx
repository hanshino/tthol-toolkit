import { priceText } from './format';

type Priced = Parameters<typeof priceText>[0];

export function PriceCell({ p }: { p: Priced }) {
  const t = priceText(p);
  return (
    <span className="mk-price" data-kind={t.kind}>
      <span className="mk-price-main">{t.main}</span>
      {t.sub && <span className="mk-price-sub">{t.sub}</span>}
    </span>
  );
}
