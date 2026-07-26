"use client";

import Link from "next/link";
import React from "react";
import { useRouter } from "next/navigation";

import { randomEnrichedDate, type DiscoveryState } from "@/src/lib/coverage";
import { formatPublicDate } from "@/src/lib/date";
import { markNavigation } from "@/src/lib/travel-store";
import styles from "./DayNavigation.module.css";

/**
 * Evidence discovery, as a navigation family of its own.
 *
 * Deliberately separate from the chronological bar: previous/next day move
 * through time, previous/next enriched move toward evidence. Collapsing
 * them would silently change what an existing control means, which is the
 * one thing this navigation must not do.
 */
export function EnrichedNavigation({
  date,
  state,
  // Imported rather than passed in: a function cannot cross the server /
  // client boundary as a prop. Tests override it directly.
  resolveRandom = randomEnrichedDate
}: {
  date: string;
  state: DiscoveryState;
  resolveRandom?: () => Promise<string | null>;
}) {
  const router = useRouter();
  const [pending, setPending] = React.useState(false);

  // Nothing to discover and nowhere to send anyone: render no controls at
  // all rather than a row of dead buttons.
  if (!state.hasAnyEnrichedDestination) {
    return null;
  }

  const monument = formatPublicDate(date) ?? date;

  return (
    <nav
      aria-label="Evidence discovery"
      className={styles.bar}
      data-testid="enriched-nav"
    >
      {state.before ? (
        <Link
          className={styles.step}
          href={"/day/" + state.before.date}
          aria-label={
            "Previous enriched date, " +
            (formatPublicDate(state.before.date) ?? state.before.date) +
            (state.before.distance ? ", " + state.before.distance : "")
          }
          onClick={() => markNavigation()}
        >
          <span aria-hidden="true">←</span> Previous enriched date
        </Link>
      ) : null}
      {resolveRandom ? (
        <button
          className={styles.step}
          disabled={pending}
          type="button"
          onClick={() => {
            setPending(true);
            void resolveRandom()
              .then((target) => {
                if (target) {
                  markNavigation();
                  router.push("/day/" + target);
                }
              })
              .finally(() => setPending(false));
          }}
        >
          Random enriched date
        </button>
      ) : null}
      {state.after ? (
        <Link
          className={styles.step}
          href={"/day/" + state.after.date}
          aria-label={
            "Next enriched date, " +
            (formatPublicDate(state.after.date) ?? state.after.date) +
            (state.after.distance ? ", " + state.after.distance : "")
          }
          onClick={() => markNavigation()}
        >
          Next enriched date <span aria-hidden="true">→</span>
        </Link>
      ) : null}
      <p className={styles.absenceNote} data-testid="enriched-nav-absence">
        {state.missingDirection === "after"
          ? "No reviewed enriched date is currently published after " +
            monument +
            "."
          : state.missingDirection === "before"
            ? "No reviewed enriched date is currently published before " +
              monument +
              "."
            : ""}
      </p>
    </nav>
  );
}
