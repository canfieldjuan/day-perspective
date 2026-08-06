"use client";

import React, { useEffect, useRef, useState, type ReactNode } from "react";
import {
  PUBLIC_DATE_MAX,
  PUBLIC_DATE_MIN,
  classifyPublicDateInput,
  formatPublicDate,
  isSupportedPublicDate
} from "@/src/lib/date";
import {
  isProfileNotPublished,
  isPublishedProfileResponse
} from "@/src/lib/day-profile";
import type { PublishedDayProfile } from "@day-perspective/contracts";
import { ProfileSections } from "./ProfileSections";
import { entryKindForArrival, phaseForView } from "@/src/lib/travel-phase";
import { hasNavigated, recordArrival } from "@/src/lib/travel-store";

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
  arrival,
  discovery
}: {
  date: string;
  arrival?: ReactNode;
  /** Evidence-discovery panel, rendered only where the page is sparse. */
  discovery?: ReactNode;
}) {
  const [state, setState] = useState<{
    date: string;
    view: ViewState;
    adjacentArrival: boolean;
    navigatedArrival: boolean;
  }>({
    date,
    view: { kind: "loading" },
    adjacentArrival: false,
    navigatedArrival: false
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
              adjacentArrival: hasNavigated() || arrivalNumber > 1,
              navigatedArrival: hasNavigated()
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
              adjacentArrival: hasNavigated() || arrivalNumber > 1,
              navigatedArrival: hasNavigated()
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
            adjacentArrival: hasNavigated() || arrivalNumber > 1,
            navigatedArrival: hasNavigated()
          });
        }
      } catch {
        if (!controller.signal.aborted) {
          const arrivalNumber = recordArrival();
          setState({
            date,
            view: { kind: "api-error" },
            adjacentArrival: hasNavigated() || arrivalNumber > 1,
            navigatedArrival: hasNavigated()
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
  const entry = entryKindForArrival(state.adjacentArrival);

  const statusLine = !isSupportedPublicDate(date)
    ? "No profile request was made for this address."
    : viewState.kind === "loading"
      ? null
      : viewState.kind === "unpublished"
        ? "No profile is published for this date."
        : viewState.kind === "api-error"
          ? "Publication status unavailable."
          : "An evidence-backed profile is published for this date.";

  const arrivalRef = useRef<HTMLElement>(null);
  const lastFocusedDateRef = useRef<string | null>(null);
  useEffect(() => {
    if (
      phase === "arrived" &&
      state.navigatedArrival &&
      lastFocusedDateRef.current !== state.date
    ) {
      lastFocusedDateRef.current = state.date;
      arrivalRef.current?.querySelector("h1")?.focus();
    }
  }, [phase, state.navigatedArrival, state.date]);

  const arrivalPanel = (
    <header
      className="masthead day-arrival"
      data-testid="day-arrival"
      ref={arrivalRef}
    >
      {arrival}
      {statusLine ? <p className="publication-status">{statusLine}</p> : null}
      <p aria-live="polite" className="visually-hidden">
        {phase === "arrived"
          ? "Arrived at " + (formatPublicDate(date) ?? date) + "."
          : ""}
      </p>
    </header>
  );

  if (!isSupportedPublicDate(date)) {
    const inputClass = classifyPublicDateInput(date);
    return (
      <div
        className="travel-shell"
        data-entry={entry}
        data-phase={phase}
        data-testid="travel-shell"
      >
        {arrivalPanel}
        <section className="state-panel state-panel--error" aria-labelledby="invalid-date-title">
          <p className="eyebrow">
            {inputClass === "out-of-range"
              ? "Date outside the public range"
              : "Not a calendar date"}
          </p>
          {inputClass === "out-of-range" ? (
            <>
              <h2 id="invalid-date-title">This date is outside the public range.</h2>
              <p>
                Records span {PUBLIC_DATE_MIN} through {PUBLIC_DATE_MAX}.
              </p>
            </>
          ) : (
            <>
              <h2 id="invalid-date-title">This address is not a calendar date.</h2>
              <p>
                Use the form YYYY-MM-DD, between {PUBLIC_DATE_MIN} and{" "}
                {PUBLIC_DATE_MAX}.
              </p>
            </>
          )}
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
                adjacentArrival: previous.adjacentArrival,
                navigatedArrival: previous.navigatedArrival
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
          sourceAttributions={viewState.profile.source_attributions}
          quality={viewState.profile.quality}
          profileDate={date}
          publicationManifestId={viewState.manifestId}
          publicationContentHash={viewState.contentHash}
        />
        {discovery}
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
