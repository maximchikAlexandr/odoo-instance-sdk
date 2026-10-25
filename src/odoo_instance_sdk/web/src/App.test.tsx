import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { formatLocalTimestamp } from "./format";
import {
  getMonitorSnapshot,
  GitActivityState,
  openPgAdmin,
  startRuntime,
  stopRuntime,
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
  startRuntime: vi.fn(),
  stopRuntime: vi.fn(),
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
  schema_version: 4,
  generated_at: "2026-08-24T00:00:00Z",
  projects: [
    {
      id: "project_a", name: "alpha", display_hint: "a", repository_root: "/tmp/alpha", environment_count: 1,
      cluster: {
        mode: "compose", owned: true, state: PostgresClusterState.HEALTHY, endpoint: { host: "127.0.0.1", port: 5432 },
        container: { id: "abc123", name: "postgres", image: "postgres:16", pid: 42, pid_scope: PidScope.DOCKER_VM },
        metrics: { cpu_percent: 1, memory_usage_bytes: 1024, memory_limit_bytes: 2048, volume_usage_bytes: null, sampled_at: "2026-08-24T00:00:00Z" },
        unavailability_reason: null, sampled_at: "2026-08-24T00:00:00Z",
      },
      runtime: { state: RuntimeState.NOT_READY, root_pid: 21, child_pids: [], process_count: 1,
        cpu_percent: null, memory_bytes: null, started_at: null, http_url: null,
        http_port: null, database_name: null, commit_sha: null, branch: "main" },
      storage: { total_bytes: 2048, complete: true, backup_bytes: 1024 },
      publication: { state: "unpublished", external_url: null, reason: null, route_identity: null },
    },
    { id: "project_b", name: "beta", display_hint: "b", repository_root: "/tmp/beta", environment_count: 1, cluster: null, runtime: null,
      storage: { total_bytes: null, complete: false },
      publication: { state: "unpublished", external_url: null, reason: null, route_identity: null } },
    {
      id: "project_c", name: "gamma", display_hint: "c", repository_root: "/tmp/gamma", environment_count: 0,
      cluster: {
        mode: "compose", owned: true, state: PostgresClusterState.STARTING, endpoint: { host: "127.0.0.1", port: 5433 },
        container: null, metrics: null, unavailability_reason: "stats_failed", sampled_at: null,
      },
      runtime: { state: RuntimeState.READY, root_pid: 31, child_pids: [], process_count: 1,
        cpu_percent: 2.5, memory_bytes: 4096, started_at: null, http_url: "http://127.0.0.1:8071",
        http_port: 8071, database_name: "project_c", commit_sha: "def", branch: "main" },
      storage: { total_bytes: 1048576, complete: true, backup_bytes: 235520 },
      publication: { state: "available", external_url: "https://project-c.example.test", reason: null, route_identity: "route-c" },
    },
  ],
  environments: [
    {
      id: "a", project_id: "project_a", name: "ready-env", branch: "main", short_sha: "abc1234",
      db_mode: "shared", database: "a", lifecycle_state: EnvironmentState.READY, allocated_http_port: 8069,
      observed_port: PortObservation.FREE,
      publication: { state: "available", external_url: "https://env-a.example.test", reason: null, route_identity: "route-a" },
      artifacts: { worktree_exists: true, worktree_registered: true, config_exists: true,
        python_exists: true, python_contained: true, dependency_lock_exists: true, backup_exists: null },
      runtime: { state: RuntimeState.READY, root_pid: 11, child_pids: [12, 13], process_count: 3,
        cpu_percent: 1, memory_bytes: 1024, started_at: null, http_url: "http://127.0.0.1:8069",
        http_port: 8069, database_name: "a", commit_sha: "abc", branch: "main" },
      pgadmin: { state: PgAdminEligibilityState.ELIGIBLE },
      git: { default_branch: "main", head_sha: null, short_sha: null, branch: "main", ahead: null, behind: null, diff: null, state: GitActivityState.ORPHAN },
      storage: { total_bytes: 0, complete: true, worktree_bytes: null, python_environment: { owned: false, bytes: null }, database: { owned: false, postgres_bytes: null, filestore_bytes: null, total_bytes: null }, other_files_bytes: null },
    },
    {
      id: "b", project_id: "project_b", name: "stopped-env", branch: "main", short_sha: "def5678",
      db_mode: "shared", database: "b", lifecycle_state: EnvironmentState.READY, allocated_http_port: 8070,
      observed_port: null,
      publication: { state: "unpublished", external_url: null, reason: null, route_identity: null },
      artifacts: { worktree_exists: false, worktree_registered: false, config_exists: false,
        python_exists: false, python_contained: true, dependency_lock_exists: false, backup_exists: null },
      runtime: { state: RuntimeState.STOPPED, root_pid: null, child_pids: [], process_count: 0,
        cpu_percent: null, memory_bytes: null, started_at: null, http_url: null,
        http_port: null, database_name: null, commit_sha: null, branch: null },
      pgadmin: { state: PgAdminEligibilityState.ENVIRONMENT_NOT_READY },
      git: { default_branch: "main", head_sha: null, short_sha: null, branch: "main", ahead: null, behind: null, diff: null, state: GitActivityState.ORPHAN },
      storage: { total_bytes: 0, complete: true, worktree_bytes: null, python_environment: { owned: false, bytes: null }, database: { owned: false, postgres_bytes: null, filestore_bytes: null, total_bytes: null }, other_files_bytes: null },
    },
  ],
};

type SnapshotResponse = { data: Snapshot; request: Request; response: Response };
function snapshotResponse(value: Snapshot): SnapshotResponse {
  return { data: value, request: new Request("http://localhost"), response: new Response() };
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
  it("links to same-origin Admin and renders proven or unknown backup freshness", async () => {
    const downloadedAt = "2026-08-19T00:00:00Z";
    const fresh: Snapshot = {
      ...snapshot,
      projects: snapshot.projects.map((project) => project.id === "project_c"
        ? { ...project, backup_freshness: { downloaded_at: downloadedAt, age_seconds: 432000 } }
        : project),
      environments: snapshot.environments.map((environment) => environment.id === "a"
        ? { ...environment, backup_freshness: { downloaded_at: downloadedAt, age_seconds: 432000 } }
        : environment),
    };
    vi.mocked(getMonitorSnapshot).mockResolvedValue(snapshotResponse(fresh) as never);
    render(<MantineProvider><App /></MantineProvider>);

    expect(await screen.findAllByText(formatLocalTimestamp(downloadedAt))).toHaveLength(2);
    expect(document.querySelectorAll(`time[datetime="${downloadedAt}"]`)).toHaveLength(2);
    expect(screen.getAllByText("(5 days old)")).toHaveLength(2);
    expect(screen.getAllByText("Backup download date unknown")).toHaveLength(3);
    expect(screen.getByRole("link", { name: "Admin" }).getAttribute("href")).toBe("/admin/");
  });

  it("renders a future download timestamp with a clamped zero age", async () => {
    const downloadedAt = "2026-08-25T00:00:00Z";
    const future: Snapshot = {
      ...snapshot,
      projects: snapshot.projects.map((project) => project.id === "project_c"
        ? { ...project, backup_freshness: { downloaded_at: downloadedAt, age_seconds: 0 } }
        : project),
    };
    vi.mocked(getMonitorSnapshot).mockResolvedValue(snapshotResponse(future) as never);
    render(<MantineProvider><App /></MantineProvider>);

    expect(await screen.findByText(formatLocalTimestamp(downloadedAt))).toBeTruthy();
    expect(screen.getByText("(0 seconds old)")).toBeTruthy();
  });

  it("uses the same compact cards for projects and worktrees and preserves publication navigation", async () => {
    vi.mocked(getMonitorSnapshot).mockResolvedValue(snapshotResponse(snapshot) as never);
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<MantineProvider><App /></MantineProvider>);

    await screen.findByText("ready-env");
    const projects = screen.getAllByTestId("project-card");
    const environments = screen.getAllByTestId("environment-card");
    expect(projects).toHaveLength(3);
    expect(environments).toHaveLength(2);
    expect(projects[2].textContent).toContain("Main checkout");
    expect(projects[2].textContent).toContain("project_c");
    expect(projects[2].textContent).toContain("1.0 MiB");
    expect(projects[2].textContent).toContain("230 KiB backups, project total");
    expect(projects[2].textContent).toContain("Disk");
    expect(projects[2].textContent).toContain("http://127.0.0.1:8071");
    expect(environments[0].textContent).toContain("Worktree");
    expect(environments[0].textContent).toContain("Disk");
    expect(environments[0].textContent).not.toContain("Resources");
    expect(projects[1].textContent).toContain("(incomplete)");
    expect(projects[1].textContent).toContain("project total");
    expect(projects[2].querySelector(".instance-branch")?.textContent).toBe("Branch main");
    expect(projects[0].querySelector(".instance-branch")?.textContent).toBe("Branch main");
    expect(environments[1].querySelector(".instance-branch")?.textContent).toBe("Branch main");
    for (const card of [...projects, ...environments]) {
      expect(card.className).toContain("instance-card");
      expect(card.querySelectorAll(".instance-status")).toHaveLength(1);
      expect(card.textContent).not.toMatch(/pgAdmin|publication:|port:|container:|PID|disk:|venv|git:/);
    }

    const projectButtons = screen.getAllByTestId("open-project-odoo") as HTMLButtonElement[];
    expect(projectButtons[0].disabled).toBe(true);
    expect(projectButtons[2].disabled).toBe(false);
    fireEvent.click(projectButtons[2]);
    expect(open).toHaveBeenCalledWith("https://project-c.example.test", "_blank", "noopener,noreferrer");
    const buttons = screen.getAllByTestId("open-odoo") as HTMLButtonElement[];
    expect(buttons[1].disabled).toBe(true);
    fireEvent.click(buttons[0]);
    expect(open).toHaveBeenCalledWith("https://env-a.example.test", "_blank", "noopener,noreferrer");

    fireEvent.click(screen.getByRole("textbox", { name: "Project" }));
    fireEvent.click(await screen.findByText("alpha (a)"));
    await waitFor(() => expect(screen.getAllByTestId("environment-card")).toHaveLength(1));
    expect(screen.getAllByTestId("project-card")).toHaveLength(1);
    expect(screen.getByText("beta (b)")).toBeTruthy();
    expect(getMonitorSnapshot).toHaveBeenCalledWith(expect.objectContaining({ throwOnError: true }));
  });

  it("composes the running filter with project selection without another snapshot request", async () => {
    vi.mocked(getMonitorSnapshot).mockResolvedValue(snapshotResponse(snapshot) as never);
    render(<MantineProvider><App /></MantineProvider>);
    await screen.findByText("ready-env");
    fireEvent.click(screen.getByRole("textbox", { name: "Project" }));
    fireEvent.click(await screen.findByText("alpha (a)"));
    fireEvent.click(screen.getByRole("textbox", { name: "Instances" }));
    fireEvent.click(await screen.findByRole("option", { name: "Running" }));
    await waitFor(() => expect(screen.getByText("alpha · 2 instances")).toBeTruthy());
    expect(screen.getAllByTestId("project-card")).toHaveLength(1);
    expect(screen.getAllByTestId("environment-card")).toHaveLength(1);
    expect(getMonitorSnapshot).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("textbox", { name: "Project" }));
    fireEvent.click(await screen.findByText("beta (b)"));
    await waitFor(() => expect(screen.getByText("No running instances in this selection.")).toBeTruthy());
    expect(screen.queryAllByTestId("environment-card")).toHaveLength(0);
    expect(getMonitorSnapshot).toHaveBeenCalledTimes(1);
  });

  it.each(Object.values(PgAdminEligibilityState))("hides pgAdmin for eligibility %s", async (state) => {
    vi.mocked(getMonitorSnapshot).mockResolvedValue(snapshotResponse(snapshotForState(state)) as never);
    render(<MantineProvider><App /></MantineProvider>);
    await screen.findByTestId("environment-card");
    expect(screen.queryByTestId("open-pgadmin")).toBeNull();
    expect(screen.queryByTestId("pgadmin-state")).toBeNull();
    expect(screen.queryByTestId("pgadmin-error")).toBeNull();
    expect(openPgAdmin).not.toHaveBeenCalled();
  });

  it("does not render empty metrics or technical storage fields", async () => {
    const environment = snapshot.environments[0];
    vi.mocked(getMonitorSnapshot).mockResolvedValue(snapshotResponse({
      ...snapshot,
      environments: [{ ...environment, runtime: { ...environment.runtime, cpu_percent: null, memory_bytes: null } }],
    }) as never);
    render(<MantineProvider><App /></MantineProvider>);
    const card = await screen.findByTestId("environment-card");
    expect(card.textContent).not.toMatch(/CPU|RAM|Volume|disk|worktree|venv|7324f72/);
    expect(card.textContent).toContain("http://127.0.0.1:8069");
  });

  it("keeps an environment lifecycle failure visible as one status", async () => {
    vi.mocked(getMonitorSnapshot).mockResolvedValue(snapshotResponse({
      ...snapshot,
      environments: [{ ...snapshot.environments[0], lifecycle_state: EnvironmentState.FAILED }],
    }) as never);
    render(<MantineProvider><App /></MantineProvider>);
    const card = await screen.findByTestId("environment-card");
    expect(card.querySelector(".instance-status")?.textContent).toBe("Failed");
    expect(card.querySelectorAll(".instance-status")).toHaveLength(1);
  });

  it.each(["project", "environment"] as const)("switches Start/Stop for %s and prevents repeated requests", async (ownerKind) => {
    const running = { ...snapshot, projects: [snapshot.projects[2]], environments: [snapshot.environments[0]] };
    vi.mocked(getMonitorSnapshot).mockResolvedValue(snapshotResponse(running) as never);
    let finish!: (value: unknown) => void;
    vi.mocked(stopRuntime).mockImplementation(() => new Promise(resolve => { finish = resolve; }) as never);
    vi.mocked(startRuntime).mockResolvedValue({ data: { state: "started" } } as never);
    render(<MantineProvider><App /></MantineProvider>);
    const card = await screen.findByTestId(ownerKind === "project" ? "project-card" : "environment-card");
    const button = card.querySelector('[data-testid="runtime-control"]') as HTMLButtonElement;
    expect(button.textContent).toBe("Stop");
    fireEvent.click(button);
    fireEvent.click(button);
    expect(button.disabled).toBe(true);
    expect(button.textContent).toBe("Stopping…");
    expect(stopRuntime).toHaveBeenCalledTimes(1);
    expect(stopRuntime).toHaveBeenCalledWith(expect.objectContaining({ body: { owner_kind: ownerKind, owner_id: ownerKind === "project" ? "project_c" : "a" } }));
    const stopped = {
      ...running,
      projects: running.projects.map(p => ({ ...p, runtime: { ...p.runtime!, state: RuntimeState.STOPPED } })),
      environments: running.environments.map(e => ({ ...e, runtime: { ...e.runtime, state: RuntimeState.STOPPED } })),
    };
    vi.mocked(getMonitorSnapshot).mockResolvedValue(snapshotResponse(stopped) as never);
    finish({ data: { state: "stopped" } });
    await waitFor(() => expect(button.textContent).toBe("Start"));
    expect(card.textContent).toContain("Disk");
    expect(button.disabled).toBe(false);
    vi.mocked(getMonitorSnapshot).mockResolvedValue(snapshotResponse(running) as never);
    fireEvent.click(button);
    await waitFor(() => expect(button.textContent).toBe("Stop"));
    expect(startRuntime).toHaveBeenCalledWith(expect.objectContaining({ body: { owner_kind: ownerKind, owner_id: ownerKind === "project" ? "project_c" : "a" } }));
  });

  it("shows a rejected runtime action and preserves the current control", async () => {
    vi.mocked(getMonitorSnapshot).mockResolvedValue(snapshotResponse(snapshot) as never);
    vi.mocked(stopRuntime).mockRejectedValue({ code: "runtime_conflict", message: "Runtime action rejected." });
    render(<MantineProvider><App /></MantineProvider>);
    await screen.findByText("gamma");
    const card = screen.getAllByTestId("project-card")[2];
    const button = card.querySelector('[data-testid="runtime-control"]') as HTMLButtonElement;
    fireEvent.click(button);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Runtime action rejected.");
    expect(button.textContent).toBe("Stop");
    expect(button.disabled).toBe(false);
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
});
