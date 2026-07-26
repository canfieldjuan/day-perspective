"use client";

import Link from "next/link";
import React from "react";

import type { DiscoveryState, EnrichedDestination } from "@/src/lib/coverage";
import { formatPublicDate } from "@/src/lib/date";
import { markNavigation } from "@/src/lib/travel-store";
import {
  describeDestination,
  describeDestinationLabel,
  describeDestinationLead,
  describeEmptyIndex,
  describeMissingDirection
} from "@/src/lib/coverage-copy";
import styles from "./CoverageDiscovery.module.css";

/**
 * Evidence discovery, kept deliberately separate from chronological
 * navigation (UI_UX_CONTRACT: two navigation families).
 *
 * Every destination shows its exact date as the primary information, with
 * relative distance as supporting text. The archive currently holds one
 * enriched date in 27,759, so "decades away" and "nothing in that
 * direction" are the ordinary answers here, not edge cases — and neither
 * may be dressed up as nearness.
 */
export function CoverageDiscovery({
  date,
  state
}: {
  date: string;
  state: DiscoveryState;
}) {
  // An enriched page is not sparse, and coverage we could not read is not
  // an empty archive. Neither gets a discovery prompt.
  if (state.kind === "on-enriched-date" || state.kind === "unknown") {
    return null;
  }

  const monument = formatPublicDate(date) ?? date;

  return (
    <section
      aria-labelledby="find-enriched-dates"
      className={styles.discovery}
      data-testid="coverage-discovery"
      data-discovery-state={state.kind}
    >
      <h2 className={styles.heading} id="find-enriched-dates">
        Find enriched dates
      </h2>
      {state.kind === "none-available" ? (
        <p className={styles.explanation}>{describeEmptyIndex(state)}</p>
      ) : null}
      {state.kind === "both-directions" ? (
        <BothDirections state={state} />
      ) : null}
      {state.kind === "one-direction" ? (
        <OneDirection monument={monument} state={state} />
      ) : null}
    </section>
  );
}

function BothDirections({ state }: { state: DiscoveryState }) {
  const { before, after, closer } = state;
  if (!before || !after) {
    return null;
  }
  return (
    <>
      <p className={styles.explanation}>Closest enriched dates</p>
      <ul className={styles.destinations}>
        <li>{describeDestinationLabel(before)}</li>
        <li>{describeDestinationLabel(after)}</li>
      </ul>
      <p className={styles.controls}>
        {/* Rendered in proximity order, so keyboard order genuinely reaches
            the closer destination first. An attribute alone would describe
            an ordering without producing one. Neither is preselected,
            because neither is automatically preferable. */}
        {(closer === "after"
          ? [
              { destination: after, label: "Go later", side: "after" },
              { destination: before, label: "Go earlier", side: "before" }
            ]
          : [
              { destination: before, label: "Go earlier", side: "before" },
              { destination: after, label: "Go later", side: "after" }
            ]
        ).map(({ destination, label, side }) => (
          <Link
            className={styles.jump}
            data-closer={closer === side ? "true" : undefined}
            href={"/day/" + destination.date}
            key={side}
            onClick={() => markNavigation()}
          >
            {label}
          </Link>
        ))}
      </p>
    </>
  );
}

function OneDirection({
  monument,
  state
}: {
  monument: string;
  state: DiscoveryState;
}) {
  const destination = state.before ?? state.after;
  if (!destination) {
    return null;
  }
  const target = formatPublicDate(destination.date) ?? destination.date;

  return (
    <>
      <p className={styles.lead}>{describeDestinationLead(destination)}</p>
      <p className={styles.explanation}>{describeDestination(destination)}</p>
      <p className={styles.controls}>
        <Link
          className={styles.jump}
          href={"/day/" + destination.date}
          onClick={() => markNavigation()}
        >
          {destination.band === "days" ? `Explore ${target}` : `Jump to ${target}`}
        </Link>
      </p>
      {/* An absence is explained rather than rendered as a disabled arrow
          with no reason given. */}
      {describeMissingDirection(state, monument) ? (
        <p className={styles.absence}>
          {describeMissingDirection(state, monument)}
        </p>
      ) : null}
    </>
  );
}
