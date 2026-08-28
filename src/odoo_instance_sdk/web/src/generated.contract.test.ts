/// <reference types="vite/client" />

import { expect, expectTypeOf, it } from "vitest";
import {
  getMonitorSnapshot,
  HttpErrorCode,
  openPgAdmin,
  PgAdminEligibilityState,
  PgAdminOpenState,
  type EnvironmentSnapshot,
  type PgAdminEligibility,
  type PgAdminOpenRequest,
  type PgAdminOpenResult,
} from "./generated";
import client from "./client-config";

const generatedSources = import.meta.glob("./generated/**/*.ts", {
  eager: true,
  import: "default",
  query: "?raw",
}) as Record<string, string>;
const appSources = import.meta.glob("./App.tsx", {
  eager: true,
  import: "default",
  query: "?raw",
}) as Record<string, string>;
const handwrittenApiSources = import.meta.glob("./api.ts", { eager: true, query: "?raw" });

it("exports the flat Fetch operations and all generated files have headers", () => {
  expect(typeof getMonitorSnapshot).toBe("function");
  expect(typeof openPgAdmin).toBe("function");
  expect(Object.keys(generatedSources).length).toBeGreaterThan(0);

  for (const source of Object.values(generatedSources)) {
    expect(source.startsWith("// @generated\n")).toBe(true);
    expect(source).not.toMatch(/from ['"][^'"]*(axios|react-query|tanstack|zod|mock)/i);
  }
});

it("preserves the exact generated enum values and required nullable boundaries", () => {
  expect(Object.values(HttpErrorCode)).toEqual([
    "database_not_found",
    "environment_not_found",
    "invalid_request",
    "monitor_snapshot_failed",
    "pgadmin_not_eligible",
    "pgadmin_unavailable",
  ]);
  expect(Object.values(PgAdminEligibilityState)).toEqual([
    "cluster_not_owned",
    "cluster_unhealthy",
    "database_unresolved",
    "eligible",
    "environment_not_ready",
  ]);
  expect(Object.values(PgAdminOpenState)).toEqual(["reconfigured", "reused", "started"]);

  expectTypeOf<EnvironmentSnapshot["pgadmin"]>().toEqualTypeOf<PgAdminEligibility>();
  expectTypeOf<PgAdminOpenRequest["environment_id"]>().toEqualTypeOf<string>();
  expectTypeOf<PgAdminOpenResult["url"]>().toEqualTypeOf<string>();
  expectTypeOf<PgAdminOpenResult["state"]>().toEqualTypeOf<PgAdminOpenState>();
});

it("uses the generated client boundary without handwritten endpoint contracts", () => {
  expect(client.getConfig().baseUrl).toBe("");
  const appSource = appSources["./App.tsx"];
  expect(appSource).toBeDefined();
  expect(appSource).not.toMatch(/\bfetch\s*\(/);
  expect(appSource).not.toMatch(/\binterface\s+(Snapshot|EnvironmentSnapshot|HttpError|PgAdmin)/);
  expect(handwrittenApiSources).toEqual({});
});
