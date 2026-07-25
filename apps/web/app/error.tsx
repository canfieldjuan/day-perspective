"use client";

export default function RootError({
  error,
  reset
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main className="page-shell" id="main-content">
      <section className="state-panel state-panel--error" aria-labelledby="page-error-title">
        <p className="eyebrow">Page unavailable</p>
        <h1 id="page-error-title">The page could not be rendered.</h1>
        <p>
          Please try again. This does not change the publication status of the requested
          date.
        </p>
        {error.digest ? <p>Reference: {error.digest}</p> : null}
        <button className="action-button" type="button" onClick={reset}>
          Try again
        </button>
      </section>
    </main>
  );
}
