# Container image security

This service uses a **Debian Bookworm**–based Python slim image with runtime OS updates applied during the Docker build.

## Default base image

- **Builder and runtime:** `python:3.11-slim-bookworm`
- Runtime stage runs `apt-get update`, `apt-get upgrade`, `apt-get clean`, and removes `/var/lib/apt/lists/*` to pick up security updates available at build time.

## Bookworm vs Bullseye (A/B comparison)

To compare vulnerability scan results against **Bullseye** (same Python 3.11):

```bash
docker build \
  --build-arg BUILDER_BASE=python:3.11-bullseye-slim \
  --build-arg RUNTIME_BASE=python:3.11-bullseye-slim \
  -t sales-agent:bullseye .
```

Compare with the default Bookworm build:

```bash
docker build -t sales-agent:bookworm .
```

Scan both tags with your registry scanner or [Trivy](https://github.com/aquasecurity/trivy), then choose the variant with fewer **effective** High/Critical issues and no runtime regressions. **Bookworm** is the recommended default unless Bullseye clearly wins on findings and compatibility.

## `libssh2`

`libssh2` is an OS package (Debian package name is usually `libssh2-1`). The Dockerfile emits its installed version at build time when present. CI logs also record `dpkg-query` output for the built image so findings can be tied to an exact version.

## Base image digest pinning

After validation, pin production builds to an immutable digest for traceability:

```dockerfile
# Example only — update digest when rebasing
FROM python:3.11-slim-bookworm@sha256:<digest>
```

Or pass `BUILDER_BASE` / `RUNTIME_BASE` as build args from the pipeline.

## Weekly rebuilds

[azure-pipelines.yml](../azure-pipelines.yml) includes a **scheduled** trigger (weekly) on `sandbox` so images pick up upstream Debian/Python security refreshes even without application code changes.

## CI scanning

The build stage:

1. Builds the image.
2. Prints installed `libssh2-1` version (if present).
3. Runs **Trivy** and publishes `trivy-report.json` as a pipeline artifact.
4. **Fails the build** if Trivy reports **CRITICAL** vulnerabilities (adjust severity in the pipeline if policy requires stricter gates).

## When no fix is available (“Fix available: No”)

Use a short, auditable process:

1. **Identify:** CVE, package, installed version, Debian suite (bookworm/bullseye).
2. **Check:** [Debian Security Tracker](https://security-tracker.debian.org/) and upstream advisories for fix status and applicability.
3. **Decide:**  
   - **Remediate:** newer base digest or suite when a fix exists.  
   - **Not affected:** document why (configuration, feature not used).  
   - **Risk accepted:** time-bound exception with owner, compensating controls, and review date.
4. **Optional:** attach **VEX** (e.g. OpenVEX) if your organization requires machine-readable statements for the scanner.

### Exception template (copy for tickets)

| Field | Value |
|--------|--------|
| Image digest | |
| CVE ID(s) | |
| Package | |
| Version | |
| Debian suite | |
| Scanner effective severity | |
| Fix available in distro? | Yes / No |
| Assessment | Not affected / Accepted risk / Pending upstream |
| Justification | |
| Owner | |
| Review date | |
