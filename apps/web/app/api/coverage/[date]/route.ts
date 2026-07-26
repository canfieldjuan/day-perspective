import { NextResponse } from "next/server";

const apiBaseUrl = process.env.API_BASE_URL ?? "http://localhost:8000";

/** One date's richness and the nearest dates worth travelling to. */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ date: string }> }
) {
  const { date } = await params;
  try {
    const upstream = await fetch(
      `${apiBaseUrl}/api/v1/coverage/${encodeURIComponent(date)}`,
      { cache: "no-store", headers: { Accept: "application/json" } }
    );
    const body = await upstream.text();
    return new NextResponse(body, {
      status: upstream.status,
      headers: {
        "Cache-Control": "no-store",
        "Content-Type":
          upstream.headers.get("content-type") ?? "application/json"
      }
    });
  } catch {
    return NextResponse.json(
      {
        status: "coverage_unavailable",
        message: "Archive coverage cannot be loaded right now."
      },
      { status: 503, headers: { "Cache-Control": "no-store" } }
    );
  }
}
