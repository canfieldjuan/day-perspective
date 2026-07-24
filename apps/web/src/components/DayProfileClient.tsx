"use client";

import React, { useEffect, useState } from "react";
import { isSupportedPublicDate } from "@/src/lib/date";
import {
  isProfileNotPublished,
  isPublishedProfileResponse
} from "@/src/lib/day-profile";
import type { PublishedDayProfile } from "@day-perspective/contracts";
import { ProfileSections } from "./ProfileSections";

type ViewState =
  | { kind: "loading" }
  | { kind: "unpublished" }
  | { kind: "api-error" }
  | { kind: "published"; profile: PublishedDayProfile };

async function parseJson(response: Response): Promise<unknown> {
  const body = await response.text();

  if (!body) {
    return {};
  }

  try {
    return JSON.parse(body) as unknown;
  } catch {
    return {};
  }
}

export function DayProfileClient({ date }: { date: string }) {
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [requestNumber, setRequestNumber] = useState(0);

  useEffect(() => {
    if (!isSupportedPublicDate(date)) {
      return;
    }

    const controller = new AbortController();

    async function loadProfile() {
      try {
        const response = await fetch("/api/day/" + encodeURIComponent(date), {
          headers: {
            Accept: "application/json"
          },
          signal: controller.signal
        });
        const payload = await parseJson(response);

        if (controller.signal.aborted) {
          return;
        }

        if (isProfileNotPublished(payload, date)) {
          setState({ kind: "unpublished" });
          return;
        }

        if (!response.ok || !isPublishedProfileResponse(payload, date)) {
          setState({ kind: "api-error" });
          return;
        }

        setState({ kind: "published", profile: payload.profile });
      } catch {
        if (!controller.signal.aborted) {
          setState({ kind: "api-error" });
        }
      }
    }

    void loadProfile();

    return () => {
      controller.abort();
    };
  }, [date, requestNumber]);

  if (!isSupportedPublicDate(date)) {
    return (
      <>
        <section className="state-panel state-panel--error" aria-labelledby="invalid-date-title">
          <p className="eyebrow">Date outside public shell</p>
          <h2 id="invalid-date-title">Choose a date from 1900-01-01 through 2025-12-31.</h2>
          <p>No profile request was made for this URL.</p>
        </section>
        <ProfileSections availability="unpublished" />
      </>
    );
  }

  if (state.kind === "unpublished") {
    return (
      <>
        <section
          className="state-panel state-panel--unpublished"
          aria-labelledby="unpublished-profile-title"
        >
          <p className="eyebrow">Profile not published</p>
          <h2 id="unpublished-profile-title">This day does not have a published profile yet.</h2>
          <p>
            An evidence-backed publication manifest has not been created for this date.
            No historical facts are substituted for the missing profile.
          </p>
        </section>
        <ProfileSections availability="unpublished" />
      </>
    );
  }

  if (state.kind === "api-error") {
    return (
      <>
        <section className="state-panel state-panel--error" aria-labelledby="api-error-title">
          <p className="eyebrow">Profile service unavailable</p>
          <h2 id="api-error-title">The profile could not be loaded.</h2>
          <p>
            The internal API did not return a usable publication status. No profile content
            is shown until it does.
          </p>
          <button
            className="action-button"
            type="button"
            onClick={() => {
              setState({ kind: "loading" });
              setRequestNumber((value) => value + 1);
            }}
          >
            Retry profile request
          </button>
        </section>
        <ProfileSections availability="api-error" />
      </>
    );
  }

  if (state.kind === "published") {
    return (
      <ProfileSections
        availability="published"
        sections={state.profile.sections}
        sectionStates={state.profile.section_states}
      />
    );
  }

  return (
    <>
      <section className="state-panel" aria-busy="true" aria-live="polite">
        <p className="eyebrow">Checking publication status</p>
        <div className="loading-line" />
        <div className="loading-line loading-line--short" />
      </section>
      <ProfileSections availability="loading" />
    </>
  );
}
