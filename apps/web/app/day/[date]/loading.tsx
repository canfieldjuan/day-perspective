export default function DayProfileLoading() {
  return (
    <main className="page-shell" aria-busy="true" aria-live="polite">
      <p className="eyebrow">Loading day profile</p>
      <div className="loading-line" />
      <div className="loading-line loading-line--short" />
    </main>
  );
}
