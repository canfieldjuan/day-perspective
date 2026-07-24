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
};

export function ProfileSections({ availability, sections }: ProfileSectionsProps) {
  return (
    <div className="section-grid" aria-label="Day profile sections">
      {DAY_PROFILE_SECTIONS.map((section, index) => {
        const headingId = section.id + "-heading";
        const statements = sections?.[section.key];

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
                </article>
              ))
            ) : (
              <p>
                {availability === "published"
                  ? "No evidence-backed content was published for this section."
                  : sectionMessages[availability]}
              </p>
            )}
          </section>
        );
      })}
    </div>
  );
}
