import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { fetchSnapshot, type Snapshot } from "./api";

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  fetchSnapshot: vi.fn(),
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
  schema_version: 1,
  generated_at: "2026-08-24T00:00:00Z",
  projects: [
    {
      id: "project_a", name: "alpha", display_hint: "a", environment_count: 1,
      cluster: {
        mode: "compose", owned: true, state: "healthy", endpoint: { host: "127.0.0.1", port: 5432 },
        container: { id: "abc123", name: "postgres", image: "postgres:16", pid: 42, pid_scope: "docker_vm" },
        metrics: { cpu_percent: 1, memory_usage_bytes: 1024, memory_limit_bytes: 2048, volume_usage_bytes: null, sampled_at: "2026-08-24T00:00:00Z" },
        unavailability_reason: null, sampled_at: "2026-08-24T00:00:00Z",
      },
    },
    { id: "project_b", name: "beta", display_hint: "b", environment_count: 1, cluster: null },
    {
      id: "project_c", name: "gamma", display_hint: "c", environment_count: 0,
      cluster: {
        mode: "compose", owned: true, state: "starting", endpoint: { host: "127.0.0.1", port: 5433 },
        container: null, metrics: null, unavailability_reason: "stats_failed", sampled_at: null,
      },
    },
  ],
  environments: [
    {
      id: "a", project_id: "project_a", name: "ready-env", branch: "main", short_sha: "abc1234",
      db_mode: "shared", database: "a", lifecycle_state: "ready", allocated_http_port: 8069,
      runtime: { state: "ready", root_pid: 11, child_pids: [12, 13], process_count: 3,
        cpu_percent: 1, rss_bytes: 1024, started_at: null, http_url: "http://127.0.0.1:8069",
        http_port: 8069, database_name: "a", commit_sha: "abc", branch: "main" },
      git: { default_branch: "main", head_sha: null, short_sha: null, branch: "main", ahead: null, behind: null, diff: null, state: "orphan" },
      storage: { total_bytes: 0, complete: true, worktree_bytes: null, python_environment: { owned: false, bytes: null }, database: { owned: false, postgres_bytes: null, filestore_bytes: null, total_bytes: null }, other_files_bytes: null },
    },
    {
      id: "b", project_id: "project_b", name: "stopped-env", branch: "main", short_sha: "def5678",
      db_mode: "shared", database: "b", lifecycle_state: "ready", allocated_http_port: 8070,
      runtime: { state: "stopped", root_pid: null, child_pids: [], process_count: 0,
        cpu_percent: null, rss_bytes: null, started_at: null, http_url: null,
        http_port: null, database_name: null, commit_sha: null, branch: null },
      git: { default_branch: "main", head_sha: null, short_sha: null, branch: "main", ahead: null, behind: null, diff: null, state: "orphan" },
      storage: { total_bytes: 0, complete: true, worktree_bytes: null, python_environment: { owned: false, bytes: null }, database: { owned: false, postgres_bytes: null, filestore_bytes: null, total_bytes: null }, other_files_bytes: null },
    },
  ],
};

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

describe("App", () => {
  it("keeps all selector options while filtering cards and opens only ready Odoo", async () => {
    vi.mocked(fetchSnapshot).mockResolvedValue(snapshot);
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

    fireEvent.click(screen.getByRole("textbox"));
    fireEvent.click(await screen.findByText("alpha (a)"));
    await waitFor(() => expect(screen.getAllByTestId("environment-card")).toHaveLength(1));
    expect(screen.getByText("beta (b)")).toBeTruthy();
    expect(fetchSnapshot).toHaveBeenCalledWith();
  });

  it("polls on a fixed cadence without overlap, coalesces misses, and cancels on unmount", async () => {
    vi.useFakeTimers();
    let resolveFirst!: (value: Snapshot) => void;
    vi.mocked(fetchSnapshot).mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }));
    vi.mocked(fetchSnapshot).mockResolvedValue(snapshot);
    const view = render(<MantineProvider><App /></MantineProvider>);

    expect(fetchSnapshot).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(5_500);
    expect(fetchSnapshot).toHaveBeenCalledTimes(1);

    resolveFirst(snapshot);
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(499);
    expect(fetchSnapshot).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchSnapshot).toHaveBeenCalledTimes(2);

    view.unmount();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetchSnapshot).toHaveBeenCalledTimes(2);
  });
});
