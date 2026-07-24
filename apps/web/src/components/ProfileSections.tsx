import React from "react";

import type { PublishedDayProfile } from "@day-perspective/contracts";
import { DAY_PROFILE_SECTIONS } from "@/src/lib/day-profile";

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
};

export function ProfileSections({
  availability,
  sections,
  sectionStates
}: ProfileSectionsProps) {
  return (
    <div className="section-grid" aria-label="Day profile sections">
      {DAY_PROFILE_SECTIONS.map((section, index) => {
        const headingId = section.id + "-heading";
        const statements = sections?.[section.key];
        const sectionState = sectionStates?.[section.key];

        return (
          <section className="section-card" key={section.key} aria-labelledby={headingId}>
            <span className="section-card__index">
              Section {String(index + 1).padStart(2, "0")}
            </span>
            <h2 id={headingId}>{section.title}</h2>
            {availability === "published" && statements && statements.length > 0 ? (
              statements.map((statement) => (
                <article className="profile-statement" key={statement.statement_id}>
                  <p>{statement.statement}</p>
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
                        <dt>Resolved claim</dt>
                        <dd>
                          {statement.provenance.resolved_claim.canonical_key}, version{" "}
                          {statement.provenance.resolved_claim.version}:{" "}
                          {statement.provenance.resolved_claim.rationale}
                        </dd>
                        <dt>Supporting claims</dt>
                        <dd>
                          {statement.provenance.supporting_claims.map((claim) => (
                            <span key={claim.predicate}>
                              {claim.predicate} from{" "}
                              <a href={claim.source_record_locator}>the USGS source record</a>
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
              ))
            ) : (
              <p>
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
  );
}
