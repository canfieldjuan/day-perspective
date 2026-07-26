import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

function getApiBaseUrl(): string {
  return (process.env.API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");
}

export async function GET() {
  try {
    const upstream = await fetch(
      getApiBaseUrl() + "/api/v1/coverage/enriched/random",
      { cache: "no-store", headers: { Accept: "application/json" } }
    );
    const body = await upstream.text();
    const headers = new Headers({ "Cache-Control": "no-store" });
    const contentType = upstream.headers.get("content-type");

    if (contentType) {
      headers.set("content-type", contentType);
    }

    return new Response(body, { headers, status: upstream.status });
  } catch {
    return NextResponse.json(
      {
        status: "coverage_unavailable",
        message: "The internal coverage service is unavailable."
      },
      { status: 503, headers: { "Cache-Control": "no-store" } }
    );
  }
}
