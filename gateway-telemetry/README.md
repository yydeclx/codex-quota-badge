# Local gateway telemetry

The local executable adds an authenticated, process-bound read-only `/api/system/provider-activity` endpoint. It reuses existing client-delivery parsing and the existing character estimator. It tracks at most 128 active requests, retains at most 32 finished scalar summaries for ten seconds, and allocates no per-request timers or output-text buffers. HTTP and WebSocket observations are isolated to avoid duplicate counts. The generation window starts at the first observed output item; first-output latency freezes at the first nonempty content delta. In-flight token counts are estimates and cannot reconstruct hidden upstream reasoning tokens.

The native sidebar polls at about one second while its existing foreground policy is active. A failed read backs off; samples expire after three seconds. `OPENCODEX_COMPANION_TELEMETRY=0` disables tracking at gateway startup. An official OpenCodex update may remove this local endpoint; the sidebar then falls back to completed-request statistics.

## OpenCodex 2.100.8 local candidate

Keep the tested candidate executable, its matching `smoke-result.json`, and the byte-for-byte backup `rollback-current-2.100.8.exe` together in `gateway-telemetry/build/2.100.8/`. This candidate is separate from the public 2.92.0 attachment. The settings switch selects the pair by the installed executable's hash and checks the candidate against the isolated test receipt. The 2.100.8 switch and rollback require zero active requests before changing the executable. Its transaction journal is `build/2.100.8/deployment.json`.

## 2.92.0 verification

- 29 telemetry unit tests; 209 existing request-log, stream, authentication and WebSocket tests.
- 53 test-layout/tooling and telemetry cases passed before the additional generation-window regression; TypeScript strict typecheck passed.
- 76 focused Python tests and the native disposable-host rendering test passed.
- Five switch/rollback transaction tests use only temporary executable fixtures; the actual production rollback was not exercised.
- Compiled executable passed isolated-Home, synthetic-local-upstream tests: authenticated access, waiting, stable first output, increasing counters, terminal cleanup and unchanged delivered text. Existing SSE wrapper tests verify byte preservation. The gateway's normal protocol transformations mean its full client bytes need not equal the mock upstream bytes.
- `test:changed` could not resolve a `dev` merge base in the downloaded source archive. No full upstream suite is claimed. Ten changed runtime/test files passed the upstream privacy scanner's `scanText` checks.
- `live-capture.json` contains only scalar metadata from real in-flight traffic; `final-verification.json` binds the live PID and installed binary hash to the verified deployment.
- `resource-before.json` and `resource-after-final.json` are short real-process samples with different load/uptime; their difference is not a measurement of patch overhead. `benchmark-result.json` is a synthetic module microbenchmark.

## Rollback

Run `rollback.ps1` from PowerShell 7. It verifies the retained original executable hash, waits for an idle gateway, restores the original launch executable and requests the existing protected graceful restart. It will not overwrite an unknown executable or forcibly terminate active work. Inspect `deployment.json` if the gateway remains busy or the result is uncertain. The original binary is `rollback-official-2.92.0.exe`; another copy of each replaced executable is preserved next to the installed executable.

`local-telemetry-2.92.0.patch` is version-specific. Revalidate it against a new source version rather than applying it blindly. No accounts, provider keys or production configuration are included in the patch.
