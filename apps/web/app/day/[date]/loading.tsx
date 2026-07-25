export default function DayProfileLoading() {
  return (
    <section aria-busy="true" aria-live="polite" className="state-panel">
      <p className="eyebrow">Loading day profile</p>
      <div className="loading-line" data-testid="loading-line" />
      <div className="loading-line loading-line--short" data-testid="loading-line" />
    </section>
  );
}
