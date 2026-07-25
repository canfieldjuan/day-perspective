"use client";

import Link from "next/link";
import React from "react";
import { useRouter } from "next/navigation";

import {
  adjacentPublicDate,
  formatPublicDate,
  isSupportedPublicDate,
  randomPublicDate
} from "@/src/lib/date";
import styles from "./DayNavigation.module.css";

/**
 * Persistent date navigation (UI_UX_CONTRACT C-6). Blind by design: steps
 * land on unpublished dates as a normal arrival. On an invalid reference
 * date, both steps are omitted — never labeled with an invented date —
 * while "Another date" and "Random day" remain (C-6.1).
 */
export function DayNavigation({ date }: { date: string }) {
  const router = useRouter();
  const valid = isSupportedPublicDate(date);
  const previous = valid ? adjacentPublicDate(date, -1) : null;
  const next = valid ? adjacentPublicDate(date, 1) : null;

  return (
    <nav
      aria-label="Date navigation"
      className={styles.bar}
      data-testid="day-nav"
    >
      {previous ? (
        <Link
          className={styles.step}
          href={"/day/" + previous}
          aria-label={"Previous day, " + formatPublicDate(previous)}
        >
          <span aria-hidden="true">←</span> {formatPublicDate(previous)}
        </Link>
      ) : null}
      <a className={styles.step} href="#historical-date">
        Another date
      </a>
      <button
        className={styles.step}
        type="button"
        onClick={() => {
          router.push("/day/" + randomPublicDate());
        }}
      >
        Random day
      </button>
      {next ? (
        <Link
          className={styles.step}
          href={"/day/" + next}
          aria-label={"Next day, " + formatPublicDate(next)}
        >
          {formatPublicDate(next)} <span aria-hidden="true">→</span>
        </Link>
      ) : null}
    </nav>
  );
}
