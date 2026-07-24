"use client";

export default function DayProfileError({
  error,
  reset
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <section className="state-panel state-panel--error" aria-labelledby="day-render-error-title">
      <p className="eyebrow">Day profile unavailable</p>
      <h2 id="day-render-error-title">This profile view could not be rendered.</h2>
      <p>
        Please try again. A rendering failure does not mean that a historical profile is
        published or unpublished.
      </p>
      {error.digest ? <p>Reference: {error.digest}</p> : null}
      <button className="action-button" type="button" onClick={reset}>
        Try again
      </button>
    </section>
  );
}
