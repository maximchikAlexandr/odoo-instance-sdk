import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import {
  getMonitorSnapshot,
  GitActivityState,
  HttpErrorCode,
  openPgAdmin,
  PgAdminOpenState,
  EnvironmentState,
  PgAdminEligibilityState,
  PidScope,
  PortObservation,
  PostgresClusterState,
  RuntimeState,
  type Snapshot,
} from "./generated";

vi.mock("./generated", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./generated")>()),
  getMonitorSnapshot: vi.fn(),
  openPgAdmin: vi.fn(),
}));

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }),
});

class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
Object.defineProperty(window, "ResizeObserver", { writable: true, value: ResizeObserver });
Object.defineProperty(globalThis, "ResizeObserver", { writable: true, value: ResizeObserver });
Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { value: () => {} });

const snapshot: Snapshot = {
  schema_version: 3,
  generated_at: "2026-08-24T00:00:00Z",
  projects: [
    {
      id: "project_a", name: "alpha", display_hint: "a", environment_count: 1,
      cluster: {
        mode: "compose", owned: true, state: PostgresClusterState.HEALTHY, endpoint: { host: "127.0.0.1", port: 5432 },
        container: { id: "abc123", name: "postgres", image: "postgres:16", pid: 42, pid_scope: PidScope.DOCKER_VM },
        metrics: { cpu_percent: 1, memory_usage_bytes: 1024, memory_limit_bytes: 2048, volume_usage_bytes: null, sampled_at: "2026-08-24T00:00:00Z" },
        unavailability_reason: null, sampled_at: "2026-08-24T00:00:00Z",
      },
    },
    { id: "project_b", name: "beta", display_hint: "b", environment_count: 1, cluster: null },
    {
      id: "project_c", name: "gamma", display_hint: "c", environment_count: 0,
      cluster: {
        mode: "compose", owned: true, state: PostgresClusterState.STARTING, endpoint: { host: "127.0.0.1", port: 5433 },
        container: null, metrics: null, unavailability_reason: "stats_failed", sampled_at: null,
      },
    },
  ],
  environments: [
    {
      id: "a", project_id: "project_a", name: "ready-env", branch: "main", short_sha: "abc1234",
      db_mode: "shared", database: "a", lifecycle_state: EnvironmentState.READY, allocated_http_port: 8069,
      observed_port: PortObservation.FREE,
      artifacts: { worktree_exists: true, worktree_registered: true, config_exists: true,
        python_exists: true, python_contained: true, dependency_lock_exists: true, backup_exists: null },
      runtime: { state: RuntimeState.READY, root_pid: 11, child_pids: [12, 13], process_count: 3,
        cpu_percent: 1, rss_bytes: 1024, started_at: null, http_url: "http://127.0.0.1:8069",
        http_port: 8069, database_name: "a", commit_sha: "abc", branch: "main" },
      pgadmin: { state: PgAdminEligibilityState.ELIGIBLE },
      git: { default_branch: "main", head_sha: null, short_sha: null, branch: "main", ahead: null, behind: null, diff: null, state: GitActivityState.ORPHAN },
      storage: { total_bytes: 0, complete: true, worktree_bytes: null, python_environment: { owned: false, bytes: null }, database: { owned: false, postgres_bytes: null, filestore_bytes: null, total_bytes: null }, other_files_bytes: null },
    },
    {
      id: "b", project_id: "project_b", name: "stopped-env", branch: "main", short_sha: "def5678",
      db_mode: "shared", database: "b", lifecycle_state: EnvironmentState.READY, allocated_http_port: 8070,
      observed_port: null,
      artifacts: { worktree_exists: false, worktree_registered: false, config_exists: false,
        python_exists: false, python_contained: true, dependency_lock_exists: false, backup_exists: null },
      runtime: { state: RuntimeState.STOPPED, root_pid: null, child_pids: [], process_count: 0,
        cpu_percent: null, rss_bytes: null, started_at: null, http_url: null,
        http_port: null, database_name: null, commit_sha: null, branch: null },
      pgadmin: { state: PgAdminEligibilityState.ENVIRONMENT_NOT_READY },
      git: { default_branch: "main", head_sha: null, short_sha: null, branch: "main", ahead: null, behind: null, diff: null, state: GitActivityState.ORPHAN },
      storage: { total_bytes: 0, complete: true, worktree_bytes: null, python_environment: { owned: false, bytes: null }, database: { owned: false, postgres_bytes: null, filestore_bytes: null, total_bytes: null }, other_files_bytes: null },
    },
  ],
};

type SnapshotResponse = { data: Snapshot; request: Request; response: Response };
type OpenResponse = {
  data: { state: PgAdminOpenState; url: string };
  request: Request;
  response: Response;
};

function snapshotResponse(value: Snapshot): SnapshotResponse {
  return { data: value, request: new Request("http://localhost"), response: new Response() };
}

function openResponse(state: PgAdminOpenState, url: string): OpenResponse {
  return { data: { state, url }, request: new Request("http://localhost"), response: new Response() };
}

function snapshotForState(state: PgAdminEligibilityState): Snapshot {
  return {
    ...snapshot,
    environments: [
      {
        ...snapshot.environments[0],
        id: `environment-${state}`,
        name: state,
        pgadmin: { state },
      },
    ],
  };
}

afterEach(() => { cleanup(); vi.clearAllMocks(); vi.restoreAllMocks(); vi.useRealTimers(); });

describe("App", () => {
  it("keeps all selector options while filtering cards and opens only ready Odoo", async () => {
    vi.mocked(getMonitorSnapshot).mockResolvedValue(snapshotResponse(snapshot) as never);
    vi.mocked(openPgAdmin).mockResolvedValue(
      openResponse(PgAdminOpenState.STARTED, "http://127.0.0.1:5050/") as never,
    );
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<MantineProvider><App /></MantineProvider>);

    await screen.findByText("ready-env");
    expect(screen.getAllByTestId("cluster-card")).toHaveLength(3);
    expect(screen.getAllByTestId("cluster-card")[0].textContent).toContain("Cluster — alpha");
    expect(screen.getAllByTestId("cluster-card")[1].textContent).toContain("Cluster — beta");
    expect(screen.getAllByTestId("cluster-card")[1].textContent).toContain("PostgreSQL: unavailable (manifest missing)");
    expect(screen.getAllByTestId("cluster-card")[2].textContent).toContain("container: —");
    expect(screen.getAllByTestId("cluster-card")[2].textContent).toContain("stats_failed");
    expect(screen.getAllByText(/↑— ↓—/)).toHaveLength(2);
    expect(screen.getAllByTestId("environment-card")).toHaveLength(2);
    expect(screen.getByTestId("worker-pids").textContent).toContain("workers 12, 13");
    const buttons = screen.getAllByTestId("open-odoo") as HTMLButtonElement[];
    expect(buttons[1].disabled).toBe(true);
    fireEvent.click(buttons[0]);
    expect(open).toHaveBeenCalledWith("http://127.0.0.1:8069", "_blank", "noopener,noreferrer");

    const pgAdmin = screen.getAllByTestId("open-pgadmin") as HTMLButtonElement[];
    expect(pgAdmin[0].disabled).toBe(false);
    expect(pgAdmin[1].disabled).toBe(true);
    fireEvent.click(pgAdmin[0]);
    await waitFor(() => expect(open).toHaveBeenCalledWith(
      "http://127.0.0.1:5050/",
      "_blank",
      "noopener,noreferrer",
    ));
    expect(openPgAdmin).toHaveBeenCalledWith(expect.objectContaining({
      body: { environment_id: "a" },
    }));

    fireEvent.click(screen.getByRole("textbox"));
    fireEvent.click(await screen.findByText("alpha (a)"));
    await waitFor(() => expect(screen.getAllByTestId("environment-card")).toHaveLength(1));
    expect(screen.getByText("beta (b)")).toBeTruthy();
    expect(getMonitorSnapshot).toHaveBeenCalledWith(expect.objectContaining({
      throwOnError: true,
    }));
  });

  it("polls on a fixed cadence without overlap, coalesces misses, and cancels on unmount", async () => {
    vi.useFakeTimers();
    let resolveFirst!: (value: SnapshotResponse) => void;
    vi.mocked(getMonitorSnapshot).mockImplementationOnce(() => new Promise<SnapshotResponse>((resolve) => { resolveFirst = resolve; }) as never);
    vi.mocked(getMonitorSnapshot).mockResolvedValue(snapshotResponse(snapshot) as never);
    const view = render(<MantineProvider><App /></MantineProvider>);

    expect(getMonitorSnapshot).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(5_500);
    expect(getMonitorSnapshot).toHaveBeenCalledTimes(1);

    resolveFirst(snapshotResponse(snapshot));
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(499);
    expect(getMonitorSnapshot).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(getMonitorSnapshot).toHaveBeenCalledTimes(2);

    view.unmount();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(getMonitorSnapshot).toHaveBeenCalledTimes(2);
  });

  it("retains initial snapshot errors and recovers through Retry", async () => {
    vi.mocked(getMonitorSnapshot)
      .mockRejectedValueOnce(new Error("temporary snapshot failure"))
      .mockResolvedValueOnce(snapshotResponse(snapshot) as never);
    render(<MantineProvider><App /></MantineProvider>);

    expect(await screen.findByText("API error: temporary snapshot failure")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("ready-env")).toBeTruthy();
    expect(getMonitorSnapshot).toHaveBeenCalledTimes(2);
  });

  it("disables pgAdmin for every backend-provided ineligible state", async () => {
    const states = [
      PgAdminEligibilityState.ENVIRONMENT_NOT_READY,
      PgAdminEligibilityState.DATABASE_UNRESOLVED,
      PgAdminEligibilityState.CLUSTER_NOT_OWNED,
      PgAdminEligibilityState.CLUSTER_UNHEALTHY,
    ];
    for (const state of states) {
      vi.mocked(getMonitorSnapshot).mockResolvedValueOnce(snapshotResponse(snapshotForState(state)) as never);
      const view = render(<MantineProvider><App /></MantineProvider>);
      const card = await screen.findByTestId("environment-card");
      expect(card.textContent).toContain(`pgAdmin: ${state === PgAdminEligibilityState.ENVIRONMENT_NOT_READY ? "environment is not ready" : state === PgAdminEligibilityState.DATABASE_UNRESOLVED ? "database is unresolved" : state === PgAdminEligibilityState.CLUSTER_NOT_OWNED ? "PostgreSQL cluster is not SDK-owned" : "PostgreSQL cluster is unhealthy"}`);
      expect((screen.getByTestId("open-pgadmin") as HTMLButtonElement).disabled).toBe(true);
      view.unmount();
    }
    expect(openPgAdmin).not.toHaveBeenCalled();
  });

  it("prevents duplicate pgAdmin opens while the typed operation is pending", async () => {
    let resolveOpen!: (value: unknown) => void;
    vi.mocked(getMonitorSnapshot).mockResolvedValue(snapshotResponse(snapshot) as never);
    vi.mocked(openPgAdmin).mockImplementation(() => new Promise((resolve) => { resolveOpen = resolve; }) as never);
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<MantineProvider><App /></MantineProvider>);
    const button = (await screen.findAllByTestId("open-pgadmin"))[0];

    fireEvent.click(button);
    fireEvent.click(button);
    expect(openPgAdmin).toHaveBeenCalledTimes(1);
    expect((button as HTMLButtonElement).disabled).toBe(true);

    resolveOpen(openResponse(PgAdminOpenState.REUSED, "http://localhost:5050/"));
    await waitFor(() => expect(open).toHaveBeenCalledWith(
      "http://localhost:5050/",
      "_blank",
      "noopener,noreferrer",
    ));
  });

  it("shows a sanitized typed pgAdmin error and recovers on retry", async () => {
    vi.mocked(getMonitorSnapshot).mockResolvedValue(snapshotResponse(snapshot) as never);
    vi.mocked(openPgAdmin)
      .mockRejectedValueOnce({ code: HttpErrorCode.DATABASE_NOT_FOUND, message: "secret detail" })
      .mockResolvedValueOnce(
        openResponse(PgAdminOpenState.REUSED, "http://127.0.0.1:5050/") as never,
      );
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<MantineProvider><App /></MantineProvider>);
    const button = (await screen.findAllByTestId("open-pgadmin"))[0];

    fireEvent.click(button);
    await waitFor(() => expect(screen.getByTestId("pgadmin-error").textContent).toContain("database was not found"));
    expect(screen.getByTestId("pgadmin-error").textContent).not.toContain("secret detail");
    fireEvent.click(button);
    await waitFor(() => expect(open).toHaveBeenCalledWith(
      "http://127.0.0.1:5050/",
      "_blank",
      "noopener,noreferrer",
    ));
  });

  it("rejects an unsafe typed result URL without launching a browser", async () => {
    vi.mocked(getMonitorSnapshot).mockResolvedValue(snapshotResponse(snapshot) as never);
    vi.mocked(openPgAdmin).mockResolvedValue(
      openResponse(PgAdminOpenState.STARTED, "https://evil.example/") as never,
    );
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<MantineProvider><App /></MantineProvider>);
    fireEvent.click((await screen.findAllByTestId("open-pgadmin"))[0]);

    await waitFor(() => expect(screen.getByTestId("pgadmin-error").textContent).toContain("unsafe URL"));
    expect(open).not.toHaveBeenCalled();
  });
});
