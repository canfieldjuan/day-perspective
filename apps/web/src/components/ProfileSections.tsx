import React from "react";

import type { PublishedDayProfile } from "@day-perspective/contracts";
import { DAY_PROFILE_SECTIONS } from "@/src/lib/day-profile";
import { deriveEvidenceClass } from "@/src/lib/evidence-class";

export type SectionAvailability = "loading" | "unpublished" | "api-error" | "published";

const sectionMessages: Record<Exclude<SectionAvailability, "published">, string> = {
  loading: "Checking whether evidence-backed content has been published for this section.",
  unpublished: "No evidence-backed content has been published for this section on this date.",
  "api-error":
    "Section content cannot be loaded until the internal profile service is available."
};

type ProfileSectionsProps = {
  availability: SectionAvailability;
  sections?: PublishedDayProfile["sections"];
  sectionStates?: PublishedDayProfile["section_states"];
  sourceAttribution?: PublishedDayProfile["source_attribution"];
  quality?: PublishedDayProfile["quality"];
  profileDate?: string;
};

export function ProfileSections({
  availability,
  sections,
  sectionStates,
  sourceAttribution,
  quality,
  profileDate
}: ProfileSectionsProps) {
  const profileYear = profileDate?.slice(0, 4) ?? "";

  return (
    <>
      {availability === "published" && quality ? (
        <aside className="state-panel" aria-labelledby="publication-quality-title">
          <p className="eyebrow">Publication quality</p>
          <h2 id="publication-quality-title">Grade {quality.grade}</h2>
          <p>{quality.explanation}</p>
        </aside>
      ) : null}
      {availability === "published" && sourceAttribution ? (
        <aside className="state-panel" aria-labelledby="source-attribution-title">
          <p className="eyebrow">Source attribution</p>
          <h2 id="source-attribution-title">
            <a href={sourceAttribution.url}>{sourceAttribution.name}</a>
          </h2>
          <p>Published by {sourceAttribution.publisher}.</p>
        </aside>
      ) : null}
      <div className="strata" aria-label="Day profile sections">
        {DAY_PROFILE_SECTIONS.map((section) => {
        const headingId = section.id + "-heading";
        const statements = sections?.[section.key];
        const sectionState = sectionStates?.[section.key];
        const populated =
          availability === "published" &&
          statements !== undefined &&
          statements.length > 0;
        const stratumState = populated
          ? "populated"
          : availability !== "published"
            ? availability
            : sectionState?.status === "not_yet_supported"
              ? "not_yet_supported"
              : "empty";

        return (
          <section
            className={populated ? "stratum" : "stratum stratum--seam"}
            data-testid={"stratum-" + section.key}
            data-stratum-state={stratumState}
            key={section.key}
            aria-labelledby={headingId}
          >
            <h2 id={headingId}>{section.title}</h2>
            {populated ? (
              statements.map((statement, statementIndex) => {
                const evidenceClass = deriveEvidenceClass(section.key, statement);
                const disputed =
                  (statement.provenance?.dissenting_claims.length ?? 0) > 0;
                const caveat = evidenceClass.caveat
                  ? evidenceClass.caveat.replace(
                      "{year}",
                      profileYear || "the year"
                    )
                  : null;
                const isLead =
                  section.key === "recorded_on_this_date" && statementIndex === 0;

                return (
                <article
                  className="profile-statement"
                  data-evidence-class={evidenceClass.key}
                  {...(isLead ? { "data-lead": "true" } : {})}
                  key={statement.statement_id}
                >
                  <p className="evidence-chips">
                    <span
                      className="evidence-chip"
                      data-evidence-class={evidenceClass.key}
                      data-testid="evidence-chip"
                    >
                      {evidenceClass.label}
                    </span>
                    {disputed ? (
                      <span
                        className="evidence-chip evidence-chip--disputed"
                        data-testid="evidence-chip"
                      >
                        Disputed — sources disagree
                      </span>
                    ) : null}
                  </p>
                  <p>{statement.statement}</p>
                  {caveat ? <p className="evidence-caveat">{caveat}</p> : null}
                  {statement.provenance_note ? (
                    <p className="profile-statement__provenance">{statement.provenance_note}</p>
                  ) : null}
                  {statement.details?.quality_grade ? (
                    <p className="quality-grade">
                      Evidence quality: {String(statement.details.quality_grade)}
                    </p>
                  ) : null}
                  {statement.provenance ? (
                    <details className="provenance-view">
                      <summary>Why can the app say this?</summary>
                      <dl>
                        {statement.provenance.resolved_claim ? (
                          <>
                            <dt>Resolved claim</dt>
                            <dd>
                              {statement.provenance.resolved_claim.canonical_key}, version{" "}
                              {statement.provenance.resolved_claim.version}:{" "}
                              {statement.provenance.resolved_claim.rationale}
                            </dd>
                          </>
                        ) : null}
                        {statement.provenance.derived_value ? (
                          <>
                            <dt>Derived value</dt>
                            <dd>
                              {statement.provenance.derived_value.kind}, calculation version{" "}
                              {statement.provenance.derived_value.calculation_version}
                            </dd>
                          </>
                        ) : null}
                        <dt>Supporting claims</dt>
                        <dd>
                          {statement.provenance.supporting_claims.map((claim, claimIndex) => (
                            <span
                              key={`${claim.predicate}:${claim.source_record_hash_sha256}:${claimIndex}`}
                            >
                              {claim.predicate} from{" "}
                              <a href={claim.source_record_locator}>
                                the {statement.provenance?.source_release.source} source record
                              </a>
                            </span>
                          ))}
                        </dd>
                        <dt>Dissenting claims</dt>
                        <dd>
                          {statement.provenance.dissenting_claims.length === 0
                            ? "None in this publication."
                            : `${statement.provenance.dissenting_claims.length} retained.`}
                        </dd>
                        <dt>Source release</dt>
                        <dd>
                          {statement.provenance.source_release.source}:{" "}
                          {statement.provenance.source_release.release}
                        </dd>
                        <dt>Methodology</dt>
                        <dd>
                          {statement.provenance.methodology.name}, version{" "}
                          {statement.provenance.methodology.version}
                        </dd>
                      </dl>
                    </details>
                  ) : null}
                </article>
                );
              })
            ) : (
              <p className="stratum__state">
                {availability === "published"
                  ? sectionState?.reason ??
                    "No evidence-backed content was published for this section."
                  : sectionMessages[availability]}
              </p>
            )}
          </section>
        );
        })}
      </div>
    </>
  );
}
