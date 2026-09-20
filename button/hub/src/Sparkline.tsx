type Props = {
  values: number[];
  ready: boolean;
};

export function Sparkline({ values, ready }: Props) {
  if (!ready || values.length < 2) {
    return <div className="spark skel" aria-hidden="true" />;
  }
  const max = Math.max(...values);
  const min = Math.min(...values);
  const range = Math.max(1, max - min);
  const d = values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * 100;
      const y = 18 - ((value - min) / range) * 16;
      return `${index ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  return (
    <svg className="spark" viewBox="0 0 100 20" preserveAspectRatio="none" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}
