export default function RootLoading() {
  return (
    <main className="page-shell" aria-busy="true" aria-live="polite" id="main-content">
      <p className="eyebrow">Historical perspective</p>
      <div className="loading-line" data-testid="loading-line" />
      <div className="loading-line loading-line--short" data-testid="loading-line" />
    </main>
  );
}
