export function SloganHeader({
  title,
  subtitle,
  highlight,
}: {
  title: string;
  subtitle: string;
  highlight?: string;
}) {
  return (
    <header className="mx-auto w-full max-w-6xl px-8 pb-5 pt-9">
      <h2 className="mb-1 text-[26px] font-bold tracking-tight text-brand-dark">
        {title}
      </h2>
      <p className="text-sm font-medium text-slate-500">
        {subtitle}
        {highlight && <span className="text-brand"> {highlight}</span>}
      </p>
    </header>
  );
}
