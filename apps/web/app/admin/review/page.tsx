"use client";

import React, { FormEvent, useState } from "react";

type JsonRecord = Record<string, unknown>;

const ENDPOINTS = [
  ["claims", "Imported claims"],
  ["conflicts", "Dissenting evidence"],
  ["review-tasks", "Review tasks"],
  ["releases", "Source releases"],
  ["manifests", "Publication manifests"]
] as const;

export default function ReviewPage() {
  const [token, setToken] = useState("");
  const [data, setData] = useState<Record<string, JsonRecord>>({});
  const [message, setMessage] = useState(
    "Enter the local development token to load review data."
  );

  async function request(path: string, init?: RequestInit) {
    const response = await fetch(`/api/admin/${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-Development-Review-Token": token,
        ...(init?.headers || {})
      }
    });
    const result = (await response.json()) as JsonRecord;
    if (!response.ok) {
      throw new Error(String(result.detail || "Review request failed."));
    }
    return result;
  }

  async function loadAll(event: FormEvent) {
    event.preventDefault();
    setMessage("Loading review state...");
    try {
      const entries = await Promise.all(
        ENDPOINTS.map(async ([path]) => [path, await request(path)] as const)
      );
      setData(Object.fromEntries(entries));
      setMessage("Review state loaded from the development-only API.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Review request failed.");
    }
  }

  async function decide(claimId: string, decision: "accepted" | "rejected") {
    const rationale = window.prompt(
      `Rationale for ${decision} decision (required):`
    );
    if (!rationale) return;
    try {
      await request(`claims/${claimId}/decision`, {
        method: "POST",
        body: JSON.stringify({ decision, rationale })
      });
      setMessage(`Recorded ${decision} decision for claim ${claimId}. Reload state.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Decision failed.");
    }
  }

  async function resolve(releaseId: string) {
    try {
      await request(`releases/${releaseId}/resolve`, { method: "POST", body: "{}" });
      setMessage(`Resolved accepted claims in release ${releaseId}. Reload state.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Resolution failed.");
    }
  }

  async function publish() {
    try {
      const result = await request("day/1964-03-27/publish", {
        method: "POST",
        body: "{}"
      });
      setMessage(`Published manifest ${String(result.manifest_id)}.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Publication failed.");
    }
  }

  const claims = (data.claims?.claims || []) as JsonRecord[];
  const releases = (data.releases?.releases || []) as JsonRecord[];

  return (
    <main className="page-shell admin-shell" id="main-content">
      <header className="masthead">
        <p className="eyebrow">Internal evidence review</p>
        <h1>Development review console</h1>
        <p className="lede">
          This utility uses a local development guard. It is not secure
          authentication and blocks production release until replaced.
        </p>
      </header>

      <form className="admin-token-form" onSubmit={loadAll}>
        <label htmlFor="review-token">Development review token</label>
        <input
          id="review-token"
          type="password"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          autoComplete="off"
        />
        <button className="action-button" type="submit">
          Load review state
        </button>
      </form>
      <p role="status" className="admin-status">
        {message}
      </p>

      <section className="admin-panel">
        <div className="admin-panel__heading">
          <h2>Imported claims</h2>
          <span>{claims.length} records</span>
        </div>
        <div className="admin-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Predicate</th>
                <th>Status</th>
                <th>Source record</th>
                <th>Decision</th>
              </tr>
            </thead>
            <tbody>
              {claims.map((claim) => (
                <tr key={String(claim.claim_id)}>
                  <td>{String(claim.predicate)}</td>
                  <td>{String(claim.status)}</td>
                  <td className="admin-mono">{String(claim.source_record_locator)}</td>
                  <td>
                    <button
                      type="button"
                      onClick={() => decide(String(claim.claim_id), "accepted")}
                    >
                      Accept
                    </button>{" "}
                    <button
                      type="button"
                      onClick={() => decide(String(claim.claim_id), "rejected")}
                    >
                      Reject
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="admin-panel">
        <div className="admin-panel__heading">
          <h2>Releases and resolution</h2>
          <button type="button" onClick={publish}>
            Publish reviewed golden profile
          </button>
        </div>
        {releases.map((release) => (
          <article className="admin-release" key={String(release.release_id)}>
            <div>
              <strong>{String(release.release_label)}</strong>
              <p>{String(release.claim_statuses)}</p>
            </div>
            {release.resolution_supported === true ? (
              <button
                type="button"
                onClick={() => resolve(String(release.release_id))}
              >
                Resolve accepted claims
              </button>
            ) : (
              <p>Use this source&apos;s specific review workflow to resolve it.</p>
            )}
          </article>
        ))}
      </section>

      {ENDPOINTS.slice(1).map(([path, label]) => (
        <details className="admin-panel" key={path}>
          <summary>{label}</summary>
          <pre>{JSON.stringify(data[path] || {}, null, 2)}</pre>
        </details>
      ))}
    </main>
  );
}
