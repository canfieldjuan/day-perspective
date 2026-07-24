export default function RootLoading() {
  return (
    <main className="page-shell" aria-busy="true" aria-live="polite">
      <p className="eyebrow">Historical perspective</p>
      <div className="loading-line" />
      <div className="loading-line loading-line--short" />
    </main>
  );
}
