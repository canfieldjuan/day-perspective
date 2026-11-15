"use client";

import { PUBLIC_DATE_MAX, PUBLIC_DATE_MIN } from "@day-perspective/contracts";
import React, { type FormEvent, useRef, useState } from "react";

type Claim = {
  id: string;
  predicate: string;
  status: string;
  value: { value?: unknown; wikidata_reference_count?: number } | null;
  locator: string;
};
type Candidate = {
  release_id: string;
  label: string;
  entity_id: string;
  revision: number | null;
  source_url: string | null;
  checksum: string;
  claims: Claim[];
  can_resolve: boolean;
  resolved: boolean;
  blocked: string | null;
};
type ReviewRequest = <T>(path: string, init?: RequestInit) => Promise<T>;

function predicateLabel(predicate: string) {
  return predicate.replace(/^candidate_/, "").replaceAll("_", " ");
}

function CandidateEvidence({ candidate, busy, act }: {
  candidate: Candidate;
  busy: boolean;
  act: (action: string, body?: object) => void;
}) {
  const [notes, setNotes] = useState<Record<string, string>>({});
  const source = candidate.source_url && URL.canParse(candidate.source_url)
    ? new URL(candidate.source_url) : null;

  return <>
    <h3>{candidate.label}</h3>
    <dl className="candidate-meta">
      <div><dt>Entity</dt><dd>{candidate.entity_id}</dd></div>
      <div><dt>Revision</dt><dd>{candidate.revision ?? "Unavailable"}</dd></div>
      <div><dt>Release</dt><dd>{candidate.release_id}</dd></div>
      <div><dt>Source URL</dt><dd>
        {source && ["https:", "http:"].includes(source.protocol)
          ? <a href={source.href} target="_blank" rel="noreferrer">{candidate.source_url}</a>
          : candidate.source_url ?? "Unavailable"}
      </dd></div>
      <div><dt>Checksum</dt><dd>{candidate.checksum}</dd></div>
    </dl>
    {candidate.blocked && <p role="alert">{candidate.blocked}</p>}
    <h4>Imported evidence</h4>
    <p>Review the pinned source before accepting a claim. Reference counts alone do not establish independent corroboration.</p>
    {candidate.claims.map((claim) => {
      const label = predicateLabel(claim.predicate);
      const rationale = notes[claim.id] ?? "";
      const references = claim.value?.wikidata_reference_count ?? 0;
      return <article className="candidate-claim" key={claim.id}>
        <h5>{label}</h5>
        <p>{claim.status.replaceAll("_", " ")}</p>
        <pre>{JSON.stringify(claim.value?.value ?? claim.value, null, 2)}</pre>
        <p className="admin-mono">{claim.locator}</p>
        <p>{references ? `${references} recorded ${references === 1 ? "reference" : "references"}` : "No references recorded."}</p>
        {["candidate", "in_review"].includes(claim.status) && <div className="candidate-actions">
          <label>Rationale for {label}
            <textarea value={rationale} disabled={busy}
              onChange={(event) => setNotes({ ...notes, [claim.id]: event.target.value })} />
          </label>
          {(["accepted", "rejected"] as const).map((decision) => <button
            key={decision} type="button" disabled={busy || !rationale.trim()}
            onClick={() => act(`claims/${claim.id}/decision`, { decision, rationale })}
          >{decision === "accepted" ? "Accept" : "Reject"} {label}</button>)}
        </div>}
      </article>;
    })}
    <div className="candidate-claim">
      <h4>Resolution</h4>
      {candidate.resolved && <p>Event resolved from this release.</p>}
      <button type="button" disabled={busy || !candidate.can_resolve} onClick={() => act("resolve")}>
        Resolve Wikidata claims
      </button>
      {!candidate.can_resolve && !candidate.blocked && <p>Accept the identity, name, type and occurrence claims to resolve the event.</p>}
    </div>
  </>;
}

export function WikidataReview({ request, onMutation }: { request: ReviewRequest; onMutation: (busy: boolean) => void }) {
  const [day, setDay] = useState("");
  const [rows, setRows] = useState<Candidate[] | null>(null);
  const [releaseId, setReleaseId] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const mutating = useRef(false);
  const path = `wikidata/${day}`;
  const candidate = rows?.find((row) => row.release_id === releaseId);

  async function load(event: FormEvent) {
    event.preventDefault();
    if (mutating.current || !day) return;
    const current = ++generation.current;
    setRows(null);
    setReleaseId("");
    setMessage("Loading candidates…");
    try {
      const { candidates } = await request<{ candidates: Candidate[] }>(path);
      if (current !== generation.current) return;
      setRows(candidates);
      setMessage(candidates.length ? "Choose a source release to review." : "No matching imported candidates for this date.");
    } catch (error) {
      if (current === generation.current) setMessage(String(error));
    }
  }

  async function act(action: string, body?: object) {
    if (mutating.current || !candidate) return;
    mutating.current = true;
    generation.current++;
    setBusy(true);
    onMutation(true);
    setMessage("Saving review action…");
    let result: string;
    try {
      const outcome = await request<{ status: string }>(`${path}/${candidate.release_id}/${action}`, {
        method: "POST", body: JSON.stringify(body ?? {})
      });
      result = `Recorded ${outcome.status}.`;
    } catch (error) {
      result = String(error);
    }
    try {
      const fresh = await request<{ candidates: Candidate[] }>(path);
      setRows(fresh.candidates);
      setMessage(result);
    } catch (error) {
      setRows(null);
      setMessage(`${result} Reload candidates to confirm current state. ${String(error)}`);
    } finally {
      mutating.current = false;
      setBusy(false);
      onMutation(false);
    }
  }

  return <section className="admin-panel" aria-labelledby="candidate-review-heading">
    <h2 id="candidate-review-heading">Wikidata candidate review</h2>
    <form className="candidate-actions" onSubmit={load}>
      <label>Occurrence date
        <input type="date" required min={PUBLIC_DATE_MIN} max={PUBLIC_DATE_MAX} value={day} disabled={busy}
          onChange={(event) => {
            if (mutating.current) return;
            generation.current++;
            setDay(event.target.value);
            setRows(null);
            setReleaseId("");
            setMessage("");
          }} />
      </label>
      <button className="action-button" type="submit" disabled={busy}>Load candidates</button>
    </form>
    <p role="status" className="admin-status">{message}</p>
    {!!rows?.length && <label>Source release
      <select value={releaseId} disabled={busy} onChange={(event) => {
        if (mutating.current) return;
        setReleaseId(event.target.value);
        setMessage("");
      }}>
        <option value="">Choose a release</option>
        {rows.map((row) => <option key={row.release_id} value={row.release_id}>
          {row.label} ({row.entity_id}, revision {row.revision ?? "unavailable"})
        </option>)}
      </select>
    </label>}
    {candidate && <CandidateEvidence key={candidate.release_id} candidate={candidate} busy={busy} act={act} />}
  </section>;
}
