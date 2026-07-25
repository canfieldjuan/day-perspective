import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Vitest globals are disabled, so Testing Library cannot self-register its
// per-test cleanup; without this, renders accumulate across tests in a file.
afterEach(cleanup);
