"use client";

import React, { useEffect, useState, type ReactNode } from "react";
import { isSupportedPublicDate } from "@/src/lib/date";
import {
  isProfileNotPublished,
  isPublishedProfileResponse
} from "@/src/lib/day-profile";
import type { PublishedDayProfile } from "@day-perspective/contracts";
import { ProfileSections } from "./ProfileSections";
import { entryKindForArrival, phaseForView } from "@/src/lib/travel-phase";
import { recordArrival } from "@/src/lib/travel-store";

type ViewState =
  | { kind: "loading" }
  | { kind: "unpublished" }
  | { kind: "api-error" }
  | {
      kind: "published";
      profile: PublishedDayProfile;
      manifestId: string;
      contentHash: string;
    };

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

export function DayProfileClient({
  date,
  arrival
}: {
  date: string;
  arrival?: ReactNode;
}) {
  const [state, setState] = useState<{
    date: string;
    view: ViewState;
    arrivals: number;
  }>({
    date,
    view: { kind: "loading" },
    arrivals: 0
  });
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
          {
            const arrivalNumber = recordArrival();
            setState({
              date,
              view: { kind: "unpublished" },
              arrivals: arrivalNumber
            });
          }
          return;
        }

        if (!response.ok || !isPublishedProfileResponse(payload, date)) {
          {
            const arrivalNumber = recordArrival();
            setState({
              date,
              view: { kind: "api-error" },
              arrivals: arrivalNumber
            });
          }
          return;
        }

        {
          const arrivalNumber = recordArrival();
          setState({
            date,
            view: {
              kind: "published",
              profile: payload.profile,
              manifestId: payload.manifest_id,
              contentHash: payload.content_hash
            },
            arrivals: arrivalNumber
          });
        }
      } catch {
        if (!controller.signal.aborted) {
          const arrivalNumber = recordArrival();
          setState({
            date,
            view: { kind: "api-error" },
            arrivals: arrivalNumber
          });
        }
      }
    }

    void loadProfile();

    return () => {
      controller.abort();
    };
  }, [date, requestNumber]);

  const viewState: ViewState =
    state.date === date ? state.view : { kind: "loading" };

  const phase = phaseForView(
    isSupportedPublicDate(date) ? viewState.kind : "unpublished"
  );
  const entry = entryKindForArrival(state.arrivals > 1);

  const statusLine = !isSupportedPublicDate(date)
    ? "No profile request was made for this address."
    : viewState.kind === "loading"
      ? null
      : viewState.kind === "unpublished"
        ? "No profile is published for this date."
        : viewState.kind === "api-error"
          ? "Publication status unavailable."
          : "An evidence-backed profile is published for this date.";

  const arrivalPanel = (
    <header className="masthead day-arrival" data-testid="day-arrival">
      {arrival}
      {statusLine ? <p className="publication-status">{statusLine}</p> : null}
    </header>
  );

  if (!isSupportedPublicDate(date)) {
    return (
      <div
        className="travel-shell"
        data-entry={entry}
        data-phase={phase}
        data-testid="travel-shell"
      >
        {arrivalPanel}
        <section className="state-panel state-panel--error" aria-labelledby="invalid-date-title">
          <p className="eyebrow">Date outside public shell</p>
          <h2 id="invalid-date-title">Choose a date from 1900-01-01 through 2025-12-31.</h2>
          <p>No profile request was made for this URL.</p>
        </section>
        <ProfileSections availability="unpublished" />
      </div>
    );
  }

  if (viewState.kind === "unpublished") {
    return (
      <div
        className="travel-shell"
        data-entry={entry}
        data-phase={phase}
        data-testid="travel-shell"
      >
        {arrivalPanel}
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
      </div>
    );
  }

  if (viewState.kind === "api-error") {
    return (
      <div
        className="travel-shell"
        data-entry={entry}
        data-phase={phase}
        data-testid="travel-shell"
      >
        {arrivalPanel}
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
              setState((previous) => ({
                date,
                view: { kind: "loading" },
                arrivals: previous.arrivals
              }));
              setRequestNumber((value) => value + 1);
            }}
          >
            Retry profile request
          </button>
        </section>
        <ProfileSections availability="api-error" />
      </div>
    );
  }

  if (viewState.kind === "published") {
    return (
      <div
        className="travel-shell"
        data-entry={entry}
        data-phase={phase}
        data-testid="travel-shell"
      >
        {arrivalPanel}
        <ProfileSections
          availability="published"
          sections={viewState.profile.sections}
          sectionStates={viewState.profile.section_states}
          sourceAttribution={viewState.profile.source_attribution}
          quality={viewState.profile.quality}
          profileDate={date}
          publicationManifestId={viewState.manifestId}
          publicationContentHash={viewState.contentHash}
        />
      </div>
    );
  }

  return (
    <div
      className="travel-shell"
      data-entry={entry}
      data-phase={phase}
      data-testid="travel-shell"
    >
      {arrivalPanel}
      <section className="state-panel" aria-busy="true" aria-live="polite">
        <p className="eyebrow">Checking publication status</p>
        <div className="loading-line" data-testid="loading-line" />
        <div className="loading-line loading-line--short" data-testid="loading-line" />
      </section>
      <ProfileSections availability="loading" />
    </div>
  );
}
