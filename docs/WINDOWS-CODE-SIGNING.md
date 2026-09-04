# Windows Authenticode Code Signing — research & implementation spec

**Status:** implemented — code + CI wired; SignPath onboarding pending
(`WINDOWS-CODE-SIGNING-HANDOFF.md`).
**Scope:** adding Authenticode code signing to the RiverCrossing Windows build so
Windows SmartScreen and antivirus do not flag or destroy the installer/app.
**Companion to:** `WINDOWS-CODE-SIGNING-HANDOFF.md` (handoff),
`CODE-SIGNING-POLICY.md` (the page SignPath requires).
**Supersedes:** the "Windows is unsigned by decision" note in `R-01` and
`E9.1.2`.

---

## 1 · Purpose & status

RiverCrossing's Windows build now carries Authenticode signing wiring: the NSIS
installer and the PyInstaller bootloader are signed via SignPath when the
`SIGNPATH_*` config is present, with an unsigned fallback until it lands. This
supersedes the deliberate v1 decision:

- `R-01` (requirements.md): *"unsigned NSIS .exe (no Windows cert …)"* — now the
  advisory-gated SignPath plan.
- `E9.1.2` (task-briefs.md): *"What remains here: Authenticode signing only."* —
  now wired; only the SignPath onboarding remains (see the handoff doc).

This document records the research and the implementation that landed. The
code, CI, and tests are in place; the manual SignPath onboarding and the first
signed release are covered by `WINDOWS-CODE-SIGNING-HANDOFF.md`.

**Recommendation in one line:** use **SignPath's free code-signing program for
open-source projects** (primary), and document Azure Artifact Signing and a
traditional OV/IV certificate as the paid alternatives.

---

## 2 · Executive summary

Signing the Windows build solves the *mechanical* half of "antivirus destroys
it": it replaces the **"Unknown publisher"** label with a verified publisher,
and moves a downloaded file off the unsigned hard-block path onto the
"warn-until-reputation" path. It does **not** instantly clear the "unrecognized
app" warning — that requires SmartScreen *reputation*, which accrues from clean
download volume over weeks/months. See §4 for the honest expectation table.

Three ways to get a code-signing identity, ordered by our preference:

| Option | Cost | Fit for RiverCrossing | Key trade-off |
|---|---|---|---|
| **SignPath (free OSS)** ✅ | $0 | Primary. GPL-3.0-only qualifies (verified). | Publisher shows **"SignPath Foundation"**, not the maintainer. |
| Azure Artifact Signing | ~$9.99/mo | Individual US/CA or listed-region org only | Managed HSM, but no EV; Action skips ARM runners. |
| Traditional OV/IV cert | ~$129–300/yr | Works worldwide for individuals | HSM/token + PFX-as-secret + signtool in CI. |

**EV certificates are deliberately excluded** — Microsoft removed the
"instant SmartScreen reputation" benefit in 2024, so EV now behaves identically
to OV for SmartScreen; EV is only mandatory for kernel drivers (not applicable).
**Self-signed / no signature are excluded** — Windows 11 Smart App Control blocks
them outright.

The single most important expectation to set: **signing is necessary but not
sufficient.** Plan for the "unrecognized app" warning to persist for the first
releases and to submit signed builds to Microsoft's file-submission portal (§4.5).

---

## 3 · Contract context

- `R-01` (design/docs-md/requirements.md:14) — the unsigned-Windows decision this
  doc reverses.
- `R-75` (requirements.md:95) — CI publishes *unsigned* installers on tags until
  EPIC 9 supplies signing.
- `E9.1.2` (design/docs-md/task-briefs.md:205) — "Windows installer … What
  remains here: Authenticode signing only."
- `E9.1.3` (task-briefs.md:207) — the macOS analogue (Developer ID codesign +
  notarization), whose advisory-gate + skip-when-unsigned test pattern is the
  template to mirror (§8, §9).
- `ci.yml` build jobs `build-windows-x64` / `build-windows-arm64` and the
  `release` job are where the hook points live (§8).

---

## 4 · How Windows decides to block

### 4.1 SmartScreen

SmartScreen only fires for files carrying the **Mark of the Web** (downloaded via
a browser or copied from an untrusted source) — not for locally built files or
network shares. On first run of a downloaded file, Windows queries the
reputation cloud and returns one of: **Known Good** (runs silently), **Known
Bad** (red block), **Unknown** (blue "unrecognized app" warning), **Offline**
(blue warning). It evaluates two independent signals — **publisher reputation**
(is it signed, by a known publisher?) and **file-hash reputation** (has this
exact binary been downloaded widely without incident?).

Sources: [Microsoft Defender SmartScreen overview](https://learn.microsoft.com/en-us/windows/security/operating-system-security/virus-and-threat-protection/microsoft-defender-smartscreen/), [SmartScreen reputation for Windows app developers](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation).

### 4.2 Smart App Control (Windows 11)

Smart App Control (SAC) is stricter and, on Windows 11, may supersede SmartScreen
App Reputation. SAC **blocks unsigned files outright** unless the cloud service
has positive reputation; a valid signature is the fallback that lets an
unknown-but-signed app run. Critically, SAC's signature check applies to **all
executable files** (DLLs, `.pyd` extension modules, etc.) that are loaded — not
just the entry-point `.exe`. A PyInstaller onedir therefore must sign **every PE
in the tree**, not just the launcher.

Sources: [Smart App Control FAQ](https://support.microsoft.com/en-us/windows/security/threat-malware-protection/smart-app-control-frequently-asked-questions), [SmartScreen reputation](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation), [Eric Lawrence — SmartScreen AppRep best practices](https://textslashplain.com/2024/11/15/best-practices-for-smartscreen-apprep/), [PyInstaller #6747](https://github.com/pyinstaller/pyinstaller/issues/6747).

SAC also does **not currently support ECC signatures** — use an **RSA** cert
(which SignPath/OV/EV all are).

### 4.3 EV no longer buys instant reputation

From ~2013 to ~2019 an EV certificate gave every signed file positive reputation
by default. Microsoft **removed that behavior in 2024**; EV now goes through the
same reputation-building as OV. *"EV certificates no longer bypass SmartScreen…
Paying a premium for EV solely to avoid SmartScreen warnings is no longer
justified."*

Sources: [code signing options](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options), [SmartScreen reputation](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation), [SSL.com EV](https://www.ssl.com/products/software-integrity/code-signing/ev/).

### 4.4 The honest expectation table

| Claim | Truth |
|---|---|
| Signing removes the "Unknown publisher" label | ✅ immediately |
| Signing stops the unsigned hard-block path | ✅ immediately |
| Signing clears the "unrecognized app" warning | ❌ not immediately — reputation needed |
| EV clears it faster than OV | ❌ no longer true (since 2024) |
| A *renewed* cert (same subject) builds reputation faster | ✅ yes — keep one identity across renewals |
| Timeframe to clear | weeks; "several weeks and hundreds of clean installs" |
| Low-volume niche apps reach the threshold | maybe never — submit each release (below) |

### 4.5 Submitting false positives

- **Microsoft Security Intelligence / WDSI file-submission portal** —
  `https://www.microsoft.com/en-us/wdsi/filesubmission` — used for both malware
  and false-positive/reputation submissions. Select **"Microsoft Defender
  SmartScreen"** (for the blue warning) or **Microsoft Defender Antivirus** (for
  a quarantine/red block) in the "product used to scan" dropdown.
- There is **no consumer-endpoint SmartScreen whitelist** — reputation builds
  organically; the portal is for correcting specific false determinations.

---

## 5 · Decision matrix

### 5.1 SignPath (free, PRIMARY)

Free code signing for open-source projects. The private key lives on SignPath's
HSM; CI submits the artifact and SignPath signs it server-side.

- **Eligibility (license):** project must use an **OSI-approved license without
  commercial dual-licensing**, for all components. **RiverCrossing is
  GPL-3.0-only — verified to qualify** (see §6.1).
- **Eligibility (governance):** SignPath Foundation performs a one-time
  discretionary "project reputation and control" verification; there is "no
  independent arbitration mechanism" and no obligation to accept a project.
- **Publisher name:** **"SignPath Foundation"** (not "RiverCrossing" / the
  maintainer) — the certificate is issued to the Foundation.
- **Cost:** free (both the SignPath.io subscription and the Foundation cert).

Sources: [signpath.org](https://signpath.org/), [signpath.org/terms](https://signpath.org/terms), [signpath.org/about](https://signpath.org/about), [OSS community page](https://signpath.io/solutions/open-source-community).

### 5.2 Azure Artifact Signing (rebranded from "Azure Trusted Signing")

Microsoft's managed HSM signing service.

- **Cost:** Basic **$9.99/mo** (5,000 signatures/mo); Premium $99.99/mo
  (100,000/mo); per-signature overage.
- **Requirements:** paid Azure subscription (no free/trial); identity validation
  (individual devs **US/Canada only**; orgs in a listed-region list).
- **Constraints:** **no EV** certificates; certificates valid **~3 days** →
  RFC 3161 timestamping mandatory; the GitHub Action `azure/artifact-signing-action@v2`
  **does not support Windows Arm runners** (so the ARM64 leg needs a workaround).

Sources: [overview](https://learn.microsoft.com/en-us/azure/trusted-signing/overview), [FAQ](https://learn.microsoft.com/en-us/azure/trusted-signing/faq), [pricing](https://azure.microsoft.com/en-us/pricing/details/trusted-signing/), [action README](https://github.com/Azure/artifact-signing-action).

### 5.3 Traditional OV / IV certificate

Buy a code-signing cert from a CA (SSL.com IV/OV ≈$129/yr — approximate,
unverified — plus eSigner; Sectigo, Certum — Certum explicitly issues OV to
individuals; DigiCert/GlobalSign are org-oriented).

- Since **June 2023** the CA/Browser Forum requires OV private keys on a
  **HSM or hardware token** (cloud-HSM services like SSL.com eSigner qualify);
  EV's hardware-key requirement predates that.
- In CI: store the `.pfx` as a base64 GitHub secret, `Import-PfxCertificate`,
  sign with `signtool`. Works on both x64 and ARM64 runners.

Sources: [SSL.com](https://www.ssl.com/certificates/code-signing/), [Certum](https://www.certum.eu/en/product/code-signing-certificates/), [Sectigo](https://www.sectigo.com/ssl-certificates-tls/code-signing), [code signing options](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options), [CA/Browser Forum code signing](https://cabforum.org/working-groups/code-signing/).

### 5.4 Excluded

- **EV** — no SmartScreen advantage (since 2024), requires a registered legal
  entity + hardware token, costs more. Only needed for kernel drivers.
- **Self-signed / unsigned** — blocked by Smart App Control.

---

## 6 · SignPath deep dive

### 6.1 License compliance (the review the maintainer asked for)

RiverCrossing is **GPL-3.0-only**: `pyproject.toml:15` (`license =
"GPL-3.0-only"`), a full GPLv3 `LICENSE` file, and an SPDX
`# SPDX-License-Identifier: GPL-3.0-only` line on every source file.

SignPath's acceptance rule is: *"The project must use an OSI-approved Open Source
license without commercial dual-licensing for all components."*

- **`GPL-3.0-only` appears literally** in SignPath's accepted-license data file,
  and is the **single most-represented** license among the ~332 accepted
  projects (128 GPL-3.0-only, 160 GPL-family, of 332).
- **Copyleft is not a disqualifier** — the disqualifier is *dual-licensing*
  (e.g. GPL + a paid commercial license). SignPath even cites GPL v3 §1 to
  define "System Libraries", so GPL projects are explicitly contemplated.
- **Conclusion: license compatibility is a non-issue** as long as RiverCrossing
  stays single-licensed. If it ever adds a commercial/dual-license tier, it
  becomes ineligible.

One adjacent caveat: "for all components" means the **bundled** PyInstaller
dependencies (Python runtime, wxWidgets, etc.) must individually be OSI-approved
without commercial dual-licensing. They are (PSF License, wxWindows Library
Licence, etc.), but the implementer should run the full dependency list past the
criterion before applying. The terms also permit bundling **unsigned upstream
OSS DLLs** inside a signed installer.

Sources: [signpath.org/terms](https://signpath.org/terms), raw [terms.md](https://github.com/SignPath/fdn-website/blob/main/docs/terms.md) and [licenses.yml](https://github.com/SignPath/fdn-website/blob/main/docs/_data/licenses.yml) and [projects.yml](https://github.com/SignPath/fdn-website/blob/main/docs/_data/projects.yml), [GPL-3.0 at OSI](https://opensource.org/license/gpl-3-0).

### 6.2 Governance reality (important — do not mis-state this)

- **"SignPath Foundation" is a brand, not an independent non-profit.** It is
  *"currently operated by SignPath GmbH, the company behind SignPath.io"* and
  *"will eventually become an independent entity"* — i.e., not independent yet.
- The governing **"Code of Conduct"** at `signpath.org/terms` is explicitly
  labelled **Draft**.
- The certificate is **issued to "SignPath Foundation"** (a real legal entity),
  which is therefore the **publisher** of every signed binary — not the
  maintainer and not "RiverCrossing".
- The Foundation may **pause/terminate the subscription** and **revoke the
  certificate immediately or retroactively** for Code-of-Conduct violations,
  without prior notice.

Sources: [signpath.org/about](https://signpath.org/about), [signpath.org/terms](https://signpath.org/terms), [signpath.io/terms-of-service](https://signpath.io/terms-of-service).

### 6.3 Obligations the project must accept

- **OSI-approved, no dual-licensing, no proprietary code** (§6.1).
- **Actively maintained; already released; functionality documented** on the
  download page.
- **Sign your own projects only** and **sign your own binaries only** (the
  signing team owns/maintains the repo; unsigned upstream DLLs may be bundled).
- **No hacking/security-circumvention tools** (not applicable here).
- **MFA for every team member** (on SignPath *and* the SCM).
- **Defined Author / Reviewer / Approver roles**.
- **Publish a "Code signing policy"** on the project page containing the exact
  attribution line — *"Free code signing provided by SignPath.io, certificate by
  SignPath Foundation"* — plus the team roles and a privacy statement.
- **Product name / product version metadata** on signed binaries.
- **Build provenance:** binaries must be built from source in a verifiable way
  (the SignPath GitHub App + origin verification enforce this).

Sources: [signpath.org/terms](https://signpath.org/terms), [docs.signpath.io/projects](https://docs.signpath.io/projects), [docs.signpath.io/trusted-build-systems/github](https://docs.signpath.io/trusted-build-systems/github).

### 6.4 Approval & verification model (corrected from earlier drafts)

- **Trusted-build-system verification** and **origin verification** are
  **"Required for Open Source Code Signing"** — these are the hard gates.
- **Manual approval of each signing request is also required for Open Source
  Code Signing** (per [docs.signpath.io/editions-explained](https://docs.signpath.io/editions-explained)),
  not the opt-in "Use approval process" option the product otherwise offers.
- The draft CoC's *"Don't fight the system … every release needs manual approval
  for signing"* phrasing therefore matches the Open Source edition — the
  earlier "tension" reading is resolved.
- The GitHub connector also requires, for OSS projects, that **all jobs of the
  workflow leading up to the signing request ran on GitHub-hosted agents** —
  satisfied by both `windows-latest` and `windows-11-arm`.

Sources: [docs.signpath.io/projects](https://docs.signpath.io/projects), [docs.signpath.io/trusted-build-systems/github](https://docs.signpath.io/trusted-build-systems/github), [docs.signpath.io/editions-explained](https://docs.signpath.io/editions-explained), [signpath.org/terms](https://signpath.org/terms).

### 6.5 CI integration (the exact mechanism)

Signing happens **server-side on SignPath's HSM**; the GitHub runner only
uploads the artifact and submits a request. The Action is
`signpath/github-action-submit-signing-request` (latest release **2.3.0**),
talking to a hosted connector at `https://githubactions.connectors.signpath.io`
(the default `connector-url`).

```yaml
steps:
  - name: Upload unsigned artifact
    id: upload-unsigned-artifact
    uses: actions/upload-artifact@v7
    with:
      path: dist/                 # the built onedir + setup.exe, zipped

  - name: Submit signing request
    uses: signpath/github-action-submit-signing-request@v2.3.0
    with:
      api-token: '${{ secrets.SIGNPATH_API_TOKEN }}'
      organization-id: '<SignPath org id>'
      project-slug: '<project slug>'
      signing-policy-slug: '<policy slug>'
      artifact-configuration-slug: '<artifact config slug>'
      github-artifact-id: '${{ steps.upload-unsigned-artifact.outputs.artifact-id }}'
      wait-for-completion: true
      output-artifact-directory: './signed'
      parameters: |
        Version: "${{ github.ref_name }}"
```

Notes that matter for a PyInstaller + NSIS build:

- The artifact **must first be uploaded** with `actions/upload-artifact` and
  referenced by `github-artifact-id` (the SignPath docs example uses
  `upload-artifact@v7`).
- `upload-artifact` **zips by default**, so the SignPath artifact-configuration
  **root element must be `<zip-file>`**.
- `parameters` is a block-scalar **multiline string**, not a YAML map: one
  `name: "<JSON value>"` per line (e.g. `Version: "1.0.0"`).
- Timestamping is **automatic** (SignPath documents that "timestamps are managed
  automatically") — no manual TSA step.
- `wait-for-completion` **defaults to `true`** with a 600 s timeout that
  **fails the job** on timeout — it does not fall back to an unsigned artifact.
  `wait-for-completion-timeout-in-seconds` overrides that timeout. Other inputs:
  `connector-url`, `github-token`,
  `service-unavailable-timeout-in-seconds`,
  `download-signed-artifact-timeout-in-seconds`, and `skip-decompress`.
- There is **no standalone SignPath CLI**; the programmatic interfaces are the
  GitHub Action, the PowerShell module `SignPath` (`Install-Module -Name
  SignPath`, then `Submit-SigningRequest` / `Get-SignedArtifact`), and the REST
  API. Certificate/project/policy administration is portal-only.
- Workflow needs `permissions: { actions: read, contents: read }` for the
  connector to read the artifact (a private-repo consideration).

Sources: [GitHub integration](https://docs.signpath.io/trusted-build-systems/github), [action repo](https://github.com/SignPath/github-action-submit-signing-request), [crypto providers / timestamps](https://docs.signpath.io/crypto-providers).

### 6.6 Artifact configuration (what to sign)

**Signed scope: the NSIS `setup.exe` and the PyInstaller bootloader
`rivercrossing.exe` only.** SignPath's terms require you to *"sign your own
binaries only"* and permit bundling *"unsigned binaries of upstream OSS
projects"*, so the bundled CPython/wxWidgets/Microsoft DLLs and the `.pyd`
extension modules **stay unsigned** inside the installer — and `<pe-file>`
does not support `.pyd` anyway.

The committed reference config (`installers/signpath/artifact-config.xml`) signs
every `.exe` in the submitted ZIP and enforces the required `product-name`
metadata:

```xml
<artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1">
  <zip-file>
    <pe-file path="*.exe" max-matches="unbounded" product-name="RiverCrossing">
      <authenticode-sign />
    </pe-file>
    <pe-file path="**/*.exe" max-matches="unbounded" product-name="RiverCrossing">
      <authenticode-sign />
    </pe-file>
  </zip-file>
</artifact-configuration>
```

Wildcard semantics to get right:

- A wildcard **defaults to `max-matches="1"`** — it must match exactly one file,
  or the configuration is invalid. Set `max-matches="unbounded"` for any
  wildcard that can match more than one file.
- Recursive `**/*.exe` matches `.exe` files in subdirectories but **not** the
  zip root, so the config lists both `*.exe` (the zip root) and `**/*.exe`
  (subdirectories).

The NSIS `setup.exe` is a plain PE, so it is signed the same way as any `.exe`
(no NSIS-specific element). MSI supports "deep signing"; NSIS does not need it.

Sources: [artifact configuration reference](https://docs.signpath.io/artifact-configuration/reference), [syntax / wildcards](https://docs.signpath.io/artifact-configuration/syntax).

### 6.7 Onboarding

Application is a **HubSpot form** at `https://signpath.org/apply`. The review
timeline and verification procedure are **not publicly documented** — they come
from the application itself or `support@signpath.io`. The steps that *are*
known:

- There is **no "generate a cert" step.** On approval, SignPath provisions the
  project with the free "Open Source Code Signing" subscription, already holding
  the **SignPath Foundation certificate** — issued to **"SignPath Foundation"**,
  with the private key on their HSM. The user never imports or generates a key.
- The project then **selects that certificate in the signing-policy Certificate
  dropdown** (and enables trusted-build-system verification, origin
  verification, and manual approval).
- **Version metadata:** the installer and the app exe originally carried no
  VERSIONINFO; both now declare `ProductName`/`FileVersion`/`ProductVersion`
  (`installers/windows.nsi` `VIProductVersion` + `VIAddVersionKey`,
  `installers/rivercrossing.spec` `VSVersionInfo`), which SignPath's OSS terms
  require on signed binaries.

---

## 7 · Signing mechanics (tooling-independent facts)

These apply whether the provider is SignPath (server-side), Azure, or a
traditional cert.

### 7.1 Tools

- **`signtool.exe`** (Windows SDK) — the native Authenticode tool; already
  available on GitHub Windows runners (installable if needed).
- **`osslsigncode`** — the cross-platform OpenSSL reimplementation; only needed
  if signing off-Windows (a macOS dev box or Linux container). Not needed here —
  both CI legs are Windows runners.

Canonical `signtool` command (PFX path):

```bat
signtool sign /f MyCert.pfx /p MyPassword /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 MyFile.exe
```

Key flags: `/f` (pfx), `/p` (password), `/fd SHA256` (file digest — **required**
in modern SDK builds), `/tr` (timestamp URL) + `/td SHA256`, `/a`
(auto-select cert from store), `/as` (append signature — dual-signing), `/d`
`/du` (description / URL). Sources: [signtool](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool), [sign a file](https://learn.microsoft.com/en-us/windows/win32/seccrypto/using-signtool-to-sign-a-file).

### 7.2 SHA-256 only; timestamping mandatory

- **SHA-1 code signing is retired** (Microsoft Lifecycle, 2020–2021). Sign with
  SHA-256 only; dual-signing (SHA-1 + SHA-256) is only for Windows Vista and
  earlier — **not needed** for a Windows 10/11 app.
- **Always timestamp.** Without a timestamp the signature becomes invalid when
  the cert expires, and Windows treats the binary as unsigned. SignPath
  documents that **timestamps are managed automatically**; for the manual
  signtool/PFX path, RFC 3161 (`/tr` + `/td SHA256`) is the recommended
  protocol. Public TSAs: `http://timestamp.digicert.com`,
  `http://time.certum.pl/`, and Azure's `http://timestamp.acs.microsoft.com`
  (mandatory with Azure's 3-day certs).

Sources: [SHA-1 retired](https://learn.microsoft.com/en-us/lifecycle/announcements/sha-1-signed-content-retired), [time stamping](https://learn.microsoft.com/en-us/windows/win32/seccrypto/time-stamping-authenticode-signatures).

### 7.3 What to sign (NSIS setup.exe + PyInstaller bootloader)

- PyInstaller has **no** Windows signing — `--codesign-identity` is **macOS-only**
  (`installers/rivercrossing.spec:206` already sets `codesign_identity=None`).
  Signing is always a **post-build** step.
- Sign only **two files**: the NSIS **`setup.exe`** and the PyInstaller
  bootloader **`rivercrossing.exe`**. The bundled upstream DLLs and `.pyd`
  extension modules **stay unsigned** inside the installer (SignPath forbids
  signing upstream binaries, and `<pe-file>` does not support `.pyd`).
- **Version metadata is now present:** neither the installer nor the app exe
  originally carried VERSIONINFO; both now declare it (`installers/windows.nsi`
  `VIProductVersion`/`VIAddVersionKey`, `installers/rivercrossing.spec`
  `VSVersionInfo` on win32), satisfying SignPath's product-name/version metadata
  requirement.
- The **uninstaller** is *compiled* at build time but *written* to disk at
  install time by `WriteUninstaller` (`installers/windows.nsi:68`), and it does
  **not** inherit the installer's signature. NSIS ≥ 3.08 added
  **`!uninstfinalize`** — the *uninstaller* directive — to sign it during the
  build; `!finalize` is the equivalent *installer* directive. The repo's NSIS
  is 3.12 (x64) / 3.10 (ARM64), so it is available. If only the installer is
  signed, expect an "unknown publisher" prompt on uninstall.
- AV false positives: community advice (not PyInstaller docs) is that windowed
  mode has a *higher* detection rate; PyInstaller's own guidance is that
  code-signing "should make them less common" (not a guarantee), and the real
  fix for a specific false positive is submitting to the vendor (WDSI). **Do not
  use UPX** (pass `--noupx`; the repo already sets `upx=False` in the spec).

Sources: [PyInstaller usage](https://pyinstaller.org/en/stable/usage.html), [PyInstaller AV template](https://github.com/pyinstaller/pyinstaller/blob/develop/.github/ISSUE_TEMPLATE/antivirus.md), [NSIS signing an uninstaller](https://nsis.sourceforge.io/Signing_an_Uninstaller), [NSIS !finalize reference](https://nsis.sourceforge.io/Reference/!finalize).

### 7.4 Verifying a signature

```bat
:: exit code 0 = valid under the Default Authentication Verification Policy
signtool verify /pa /v MyFile.exe
```

```powershell
# Status property: Valid / NotSigned / HashMismatch / UnknownError / ...
(Get-AuthenticodeSignature -LiteralPath '.\RiverCrossing-setup.exe').Status
```

Note: `Valid` means "syntactically valid signature", not "trusted" — that is
exactly the right gate for "is this file signed?" (the mirror of the macOS
`spctl` check). Sources: [signtool](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool), [Get-AuthenticodeSignature](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.security/get-authenticodesignature).

---

## 8 · Local implementation map

Where signing hooks into the repo (all paths relative to repo root).

### 8.1 Build pipeline (already mapped)

- `nox -s bundle` (`noxfile.py:214`) → `pyinstaller … rivercrossing.spec` →
  `dist/rivercrossing/rivercrossing.exe` + `dist/rivercrossing/_internal/**`.
- `nox -s winsetup` (`noxfile.py:331`) → `makensis -DAPPVERSION=… -DPAYLOAD_DIR=…
  -DOUTFILE=dist/RiverCrossing-<version>-windows-<arch>-setup.exe` via
  `_find_makensis()` (`noxfile.py:295`) and `_windows_arch()` (`noxfile.py:320`).
- `nox -s winsetup_smoke` (`noxfile.py:376`) → `pytest
  tests/functional/test_winsetup_smoke.py`.
- Artifacts to sign: `dist/RiverCrossing-<version>-windows-{x64,arm64}-setup.exe`
  (the installer) and the bootloader `dist/rivercrossing/rivercrossing.exe`.

### 8.2 CI insertion points (`ci.yml`)

- `build-windows-x64` (ci.yml:242, `windows-latest`) and `build-windows-arm64`
  (ci.yml:379, `windows-11-arm`) each do: bundle → smoke → `nox -s winsetup` →
  `winsetup_smoke` → upload.
- **Signing is two-pass:** submit the bootloader `rivercrossing.exe` before
  `nox -s winsetup`, then the `setup.exe` after it — both gated on the advisory
  `signed == '1'` output.
- The `release` job runs on **`macos-latest`** (ci.yml:536) and cannot run
  Windows signing tooling — the Windows jobs must sign, or a dedicated Windows
  job does.

### 8.3 ARM64 handling (the key architectural decision)

- **SignPath:** signing is **server-side**, so the runner architecture is
  irrelevant to the signing computation — an x64 runner can submit the ARM64
  artifact. (This is a reasonable inference; SignPath publishes no explicit ARM
  statement — see §11.) The "GitHub-hosted agents" rule is satisfied by
  `windows-11-arm`.
- **Azure Artifact Signing:** the Action **does not support Windows Arm
  runners**, so the ARM64 artifact must be signed on an x64 runner
  (download → sign → re-upload). Authenticode is architecture-agnostic, so this
  works, but it needs an explicit workaround.
- **PFX + signtool:** works on both runners natively (`signtool` runs on ARM64
  via x64 emulation).

### 8.4 The macOS pattern to mirror

The macOS signing lane (`ci.yml:393-462`) is the template:

1. Declare secrets as job env (`APPLE_* = ${{ secrets.APPLE_* }}`); an unset
   secret evaluates to the empty string.
2. An **advisory-gate** step loops the secret names and writes `signed=0|1` to
   `GITHUB_OUTPUT`; a *partial* set counts as absent, so the unsigned path stays
   green until all creds land.
3. Signed steps are gated `if: steps.signing.outputs.signed == '1'`; the
   unsigned fallback is gated `!= '1'`.
4. A hard verify (`spctl --assess …`) gates the signed path.

The Windows equivalent adds the same gate to each Windows build job, runs the
SignPath Action (or signtool) in the `signed == '1'` branch, and verifies with
`signtool verify /pa` before upload.

### 8.5 Secrets to add

For **SignPath** (no private key on disk, no cert secret):
`SIGNPATH_API_TOKEN`, plus the SignPath org id / project slug / signing-policy
slug / artifact-configuration slug (these can be repo constants rather than
secrets).

For the **PFX path** (if OV/IV is chosen): base64-encoded `.pfx` +
`WIN_CERT_PASSWORD` secrets, imported with `Import-PfxCertificate` (do **not**
use `certutil`, which Microsoft does not recommend for production code).

---

## 9 · Test / gate to add

Mirror `tests/functional/test_release_signing.py` (the skip-when-unsigned idiom)
in a new `tests/functional/test_winsetup_signing.py`:

```python
def _authenticode_status(path: Path) -> str | None:
    if sys.platform != "win32":
        return None
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-AuthenticodeSignature -LiteralPath '{path}').Status"],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None

def _missing_signed_setup_reason() -> str | None:
    if sys.platform != "win32":
        return "Authenticode signing only runs on win32"
    if not SETUP_PATH.is_file():
        return f"no built installer -- run `nox -s winsetup` first; missing {SETUP_PATH}"
    if _authenticode_status(SETUP_PATH) != "Valid":
        return ("setup.exe is not Authenticode-signed -- Windows signing creds "
                "are not configured; the unsigned build ships meanwhile")
    return None

_MISSING_REASON = _missing_signed_setup_reason()

@pytest.mark.skipif(_MISSING_REASON is not None, reason=_MISSING_REASON or "no signed artifact")
def test_setup_exe_is_authenticode_signed() -> None:
    assert _authenticode_status(SETUP_PATH) == "Valid"
```

The CI gate step (signed branch) uses `signtool verify /pa /v` and fails on
non-zero — the exit-code analogue of the `spctl` assert.

---

## 10 · Step-by-step implementation checklist (for the Windows agent)

### Primary path — SignPath

1. **Confirm the 4 unknowns in §11** with SignPath (especially `.pyd`).
2. **Apply** at `https://signpath.org/apply`; provide the repo, the GPL-3.0-only
   license, and the "actively maintained / released / documented" evidence.
3. **Add the required "Code signing policy"** to the project README/home page
   with the exact attribution line and team roles.
4. **Enable MFA** on the org and define Author/Reviewer/Approver roles.
5. **Install the SignPath GitHub App** for origin verification, and create the
   SignPath project + signing policy + artifact configuration (`<zip-file>` with
   `*.exe` / `**/*.exe` signing the `setup.exe` and the `rivercrossing.exe`
   bootloader).
6. **Add the `SIGNPATH_API_TOKEN` secret** and wire the
   `signpath/github-action-submit-signing-request` steps into
   `build-windows-x64` and `build-windows-arm64` — the bootloader before
   `nox -s winsetup`, the `setup.exe` after it (mirror the §8.4 advisory gate
   so the unsigned path stays green until the token lands).
7. **Add `tests/functional/test_winsetup_signing.py`** (§9) and the
   `signtool verify /pa` gate in the signed branch.
8. **Set expectations in the user guide** — the signed-but-new app still shows
   the "unrecognized app" warning until reputation accrues; keep the
   SmartScreen "More info → Run anyway" note for early releases.
9. **Submit the first signed release** to `https://www.microsoft.com/en-us/wdsi/filesubmission`.

### Alternative path — traditional OV/IV cert + signtool (PFX)

1. Buy an OV/IV cert with an HSM/token or cloud-signing option (SSL.com/Certum).
2. Export a `.pfx`, base64 it into a GitHub secret, add `WIN_CERT_PASSWORD`.
3. In each Windows job: `Import-PfxCertificate`, sign the bootloader
   `rivercrossing.exe` and the `setup.exe` with
   `signtool sign /fd SHA256 /tr … /td SHA256`, verify with
   `signtool verify /pa /v`.
4. Add `!uninstfinalize` (NSIS ≥ 3.08) to sign the uninstaller, or accept the
   unsigned-uninstaller prompt.
5. Steps 7–9 above unchanged.

---

## 11 · Open questions for SignPath (must be resolved before implementation)

1. **OV vs EV** — which certificate class does the OSS program issue? (Not stated
   in any primary source; materially affects the SmartScreen story — an EV cert
   would carry full reputation immediately, an OV cert builds it.)
2. **`.pyd` support** — the documented `<pe-file>` extension list does **not**
   include `.pyd`. Confirm whether `.pyd` (a PE DLL) can be Authenticode-signed
   via `<pe-file>` or needs another mechanism; otherwise the PyInstaller onedir
   tree's extension modules go unsigned (a Smart App Control risk).
3. **ARM64** — confirm that signing ARM64 artifacts via the server-side flow
   (submitted from either runner) is fully supported.
4. **Onboarding** — the concrete post-application steps (review timeline,
   verification, key provisioning) are unpublished; obtain them from the
   application or `support@signpath.io`.
5. **Approval model** — reconcile the draft CoC's "every release needs manual
   approval" with the product's opt-in "Use approval process" setting.

---

## 12 · Source index

### SignPath
- https://signpath.org/ (free-for-OSS pitch)
- https://signpath.org/terms (Code of Conduct — draft; eligibility)
- https://signpath.org/about (Foundation operated by SignPath GmbH)
- https://signpath.org/apply (application form)
- https://signpath.io/solutions/open-source-community
- https://signpath.io/terms-of-service (SignPath GmbH, Vienna)
- https://docs.signpath.io/trusted-build-systems/github (GitHub Action + connector)
- https://docs.signpath.io/artifact-configuration/reference (`<pe-file>` extensions)
- https://docs.signpath.io/artifact-configuration/syntax (recursive wildcards)
- https://docs.signpath.io/crypto-providers (HSM, automatic timestamping)
- https://docs.signpath.io/projects (approval opt-in; OSS verification requirements)
- https://github.com/SignPath/github-action-submit-signing-request
- https://github.com/SignPath/fdn-website (terms.md, licenses.yml, projects.yml)

### Microsoft / Azure
- https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation
- https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options
- https://learn.microsoft.com/en-us/windows/security/operating-system-security/virus-and-threat-protection/microsoft-defender-smartscreen/
- https://support.microsoft.com/en-us/windows/security/threat-malware-protection/smart-app-control-frequently-asked-questions
- https://learn.microsoft.com/en-us/azure/trusted-signing/overview
- https://learn.microsoft.com/en-us/azure/trusted-signing/faq
- https://learn.microsoft.com/en-us/azure/trusted-signing/how-to-signing-integrations
- https://azure.microsoft.com/en-us/pricing/details/trusted-signing/
- https://github.com/Azure/artifact-signing-action
- https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool
- https://learn.microsoft.com/en-us/windows/win32/seccrypto/using-signtool-to-sign-a-file
- https://learn.microsoft.com/en-us/windows/win32/seccrypto/time-stamping-authenticode-signatures
- https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.security/get-authenticodesignature
- https://learn.microsoft.com/en-us/lifecycle/announcements/sha-1-signed-content-retired
- https://www.microsoft.com/en-us/wdsi/filesubmission

### Certificate authorities / standards
- https://www.ssl.com/certificates/code-signing/ (and IV/OV/EV subpages)
- https://www.digicert.com/signing/code-signing-certificates
- https://www.sectigo.com/ssl-certificates-tls/code-signing
- https://www.certum.eu/en/product/code-signing-certificates/
- https://www.globalsign.com/en/code-signing-certificate
- https://cabforum.org/working-groups/code-signing/

### PyInstaller / NSIS
- https://pyinstaller.org/en/stable/usage.html (`--codesign-identity` macOS-only; UPX)
- https://github.com/pyinstaller/pyinstaller/blob/develop/.github/ISSUE_TEMPLATE/antivirus.md
- https://github.com/pyinstaller/pyinstaller/issues/6747
- https://nsis.sourceforge.io/Signing_an_Uninstaller (`!uninstfinalize` NSIS 3.08+)
- https://nsis.sourceforge.io/Reference/!finalize
- https://github.com/mtrojnar/osslsigncode

### Analysis
- https://textslashplain.com/2024/11/15/best-practices-for-smartscreen-apprep/ (Eric Lawrence)

### Local (this repo)
- `design/docs-md/requirements.md` — R-01 (line 14), R-75 (line 101)
- `design/docs-md/task-briefs.md` — E9.1.2 (line 205), E9.1.3 (line 207)
- `noxfile.py` — `bundle` (214), `_find_makensis` (295), `_windows_arch` (320),
  `winsetup` (331), `winsetup_smoke` (376)
- `installers/windows.nsi` — `WriteUninstaller` (68)
- `installers/rivercrossing.spec` — `codesign_identity=None` (206)
- `.github/workflows/ci.yml` — `build-windows-x64` (242), `build-windows-arm64`
  (379), `release` (536, macos-latest), SignPath advisory gates (263, 400)
- `tests/functional/test_release_signing.py` — skip-when-unsigned idiom (66–138)
- `tests/functional/test_winsetup_smoke.py` — `SETUP_PATH`, win32 gate
