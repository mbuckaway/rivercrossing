# Windows code signing — handoff

**Status:** code + CI wired; SignPath onboarding pending. Handoff to the
maintainer (and the Windows agent, for the win32-only verification).

**Companion to:** `WINDOWS-CODE-SIGNING.md` (research/spec) and
`CODE-SIGNING-POLICY.md` (the page SignPath requires).

---

## 1 · What is already done (this branch)

- **Version metadata.** `installers/windows.nsi` now declares
  `VIProductVersion` (a 4-part `-DVERSIONINFO` define) and `VIAddVersionKey`
  for `ProductName`/`FileDescription`/`FileVersion`/`ProductVersion`;
  `installers/rivercrossing.spec` builds a PyInstaller `VSVersionInfo` from
  `rivercrossing.__version__` on win32. Both signed files now carry
  `ProductName = "RiverCrossing"`, which SignPath's OSS terms require.
- **`noxfile.py`.** `winsetup` passes `-DVERSIONINFO=<version>.0` alongside the
  existing `-DAPPVERSION`/`-DPAYLOAD_DIR`/`-DOUTFILE`.
- **CI.** `.github/workflows/ci.yml` wires an advisory gate + two SignPath
  passes into both Windows build jobs: sign the bootloader `rivercrossing.exe`
  (before `winsetup`), then sign the `setup.exe` (after), then verify both with
  `Get-AuthenticodeSignature`. The unsigned path stays green until the
  `SIGNPATH_*` config lands.
- **Reference artifact config.** `installers/signpath/artifact-config.xml` signs
  every `.exe` in the submitted ZIP and enforces `product-name="RiverCrossing"`.
- **Tests.** `tests/unit/test_windows_nsi.py` pins the new define + metadata;
  `tests/functional/test_winsetup_signing.py` skips until a signed installer
  exists.

## 2 · Manual onboarding (maintainer, not automatable)

Ordered:

1. **Apply** at `https://signpath.org/apply` (a HubSpot form). Provide the repo,
   the GPL-3.0-only license, and evidence it is maintained, released, and
   documented.
2. **Await review.** The SignPath Foundation does a one-time reputation/control
   check; the timeline is not published. Ask `support@signpath.io` if it stalls.
3. **Certificate.** There is **no "generate a cert" step.** On approval, SignPath
   provisions the organization with the free "Open Source Code Signing"
   subscription, already holding the **SignPath Foundation certificate** (issued
   to "SignPath Foundation"; the key lives on their HSM). You never import or
   generate a key.
4. **Team + MFA.** Invite the team, define Author/Reviewer/Approver roles, and
   enable MFA on both SignPath and the GitHub org (required for OSS).
5. **Project.** Create the project: name, slug, and the repository URL (needed
   for origin verification).
6. **Signing policy.** Create `release-signing` and select the SignPath
   Foundation certificate in its Certificate dropdown. Enable
   trusted-build-system verification, origin verification, and manual approval
   (all three are required for OSS).
7. **Artifact configuration.** Create it from `installers/signpath/artifact-config.xml`
   (a `<zip-file>` signing `*.exe` + `**/*.exe` with `product-name="RiverCrossing"`).
   Note its slug.
8. **Trusted build system.** Add the predefined `GitHub.com` connector to the
   organization and link it to the project; install the SignPath GitHub App and
   grant access to this repo.
9. **API token.** Create a dedicated CI user (or a token under "My profile →
   Generate token") with submitter permissions. Set it as the repo secret
   `SIGNPATH_API_TOKEN`.
10. **Repo variables.** Set `SIGNPATH_ORGANIZATION_ID`, `SIGNPATH_PROJECT_SLUG`,
    `SIGNPATH_SIGNING_POLICY_SLUG`, `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG`.
11. **Policy page.** Publish `docs/CODE-SIGNING-POLICY.md` and wire its public
    URL into the SignPath config (a README link alone is not enough).

## 3 · Open unknowns to confirm with SignPath

- **Bootloader ownership.** Whether the PyInstaller bootloader `rivercrossing.exe`
  counts as "your own binary" under the "sign your own binaries only" rule.
  Default assumption: yes (every PyInstaller app signs its launcher). Confirm at
  onboarding; if rejected, sign `setup.exe` only.
- **OV vs EV.** The Foundation certificate's validation class is not disclosed
  publicly. It changes the SmartScreen story (EV starts with reputation; OV
  builds it).
- **Wildcard depth.** Confirm `*.exe` / `**/*.exe` match the zipped
  `actions/upload-artifact` layout (validated on the first signed run).

## 4 · Release-day procedure

- Push the `v*` tag, then keep the SignPath portal open: **two** signing
  requests (bootloader, then `setup.exe`) each need a human Approver inside the
  30-minute `wait-for-completion` window; the job fails on timeout.
- If approval latency is a problem, switch to `wait-for-completion: false` and a
  submit → approve → `workflow_dispatch` download split (documented in
  `WINDOWS-CODE-SIGNING.md` §6.5).

## 5 · Windows-only verification (Windows agent)

- `pytest tests/functional/test_winsetup_signing.py` — asserts
  `Get-AuthenticodeSignature` returns `Valid` on the signed `setup.exe`.
- `signtool verify /pa /v dist/RiverCrossing-*-setup.exe` and the installed
  `rivercrossing.exe` (from a signed installer) — exit 0 = valid.
- Confirm the `.pyd`/`.dll` payload stays unsigned inside (intended; SignPath
  forbids signing them) and the app still imports/launches after the signed
  bootloader is packaged.

## 6 · Known limitations

- The NSIS **uninstaller** is unsigned (server-side signing cannot reach the
  embedded uninstaller); expect an "unknown publisher" prompt on uninstall until
  a local-signing path (`!uninstfinalize`) is adopted.
- The bundled upstream **DLLs/`.pyd`s** stay unsigned inside the installer
  (SignPath terms; "sign your own binaries only").
- A **new-but-signed** app still shows the SmartScreen "unrecognized app"
  warning until reputation accrues; submit the first release to
  `https://www.microsoft.com/en-us/wdsi/filesubmission`.

## 7 · Secrets / variables summary

| Name | Kind | Where |
|---|---|---|
| `SIGNPATH_API_TOKEN` | secret | SignPath CI-user token |
| `SIGNPATH_ORGANIZATION_ID` | variable | SignPath org id |
| `SIGNPATH_PROJECT_SLUG` | variable | project slug |
| `SIGNPATH_SIGNING_POLICY_SLUG` | variable | `release-signing` |
| `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG` | variable | artifact-config slug |
