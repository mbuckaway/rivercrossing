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

Two phases: (A) apply for the free certificate, then (B) configure signing in
the SignPath.io web app.

### A. Apply for the free Foundation certificate

1. Fill in the form at `https://signpath.org/apply` (a HubSpot form). Provide
   the repository URL, the GPL-3.0-only license, and the download/docs page
   showing the app is released and documented.
2. The SignPath Foundation runs a one-time reputation/control review. The
   timeline is not published — ask `support@signpath.io` if it stalls.
3. On approval, SignPath provisions your organization: the free **Open Source
   Code Signing** subscription plus the **SignPath Foundation** certificate
   (issued to "SignPath Foundation"; the private key lives on SignPath's HSM).
   You never create, import, or generate a certificate — it simply appears in
   your account.

### B. Configure signing in the SignPath.io web app

4. Invite the team and enable MFA on SignPath and on the GitHub org. Assign the
   Author / Reviewer / Approver roles (required for OSS).
5. Create the **Project**: name, slug, and the repository URL (the repository
   URL is required for origin verification).
6. Create the `release-signing` **signing policy** and set:
   - **Certificate** → select **SignPath Foundation** (the provisioned cert).
   - Enable **trusted build system verification**, **origin verification**, and
     the **approval process** (all three are required for OSS).
7. Create the **artifact configuration** from
   `installers/signpath/artifact-config.xml` (a `<zip-file>` signing `*.exe` +
   `**/*.exe` with `product-name="RiverCrossing"`). Note its slug.
8. Add the predefined **GitHub.com** trusted build system to the organization,
   link it to the project, and install the **SignPath GitHub App** on this repo.
9. Create a **CI user** (or a personal token under "My profile → Generate
   token") with submitter permissions. Store it as the GitHub secret
   `SIGNPATH_API_TOKEN`.
10. Set the repo variables: `SIGNPATH_ORGANIZATION_ID`, `SIGNPATH_PROJECT_SLUG`,
    `SIGNPATH_SIGNING_POLICY_SLUG`, `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG`.
11. Publish `docs/CODE-SIGNING-POLICY.md` at a public URL and wire that URL into
    the SignPath config.

> **Bring your own certificate is a different, paid path.** If you want your own
> name as publisher instead of "SignPath Foundation", you create an X.509 CSR in
> SignPath (key on the HSM), buy a cert from a CA issued to your legal entity,
> and upload it back. Not needed for the free Foundation certificate. CSR subject
> fields: Organization (O) and Country (C) are required; Common Name (CN) is
> standard; Organizational Unit / Locality / State are optional.

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
